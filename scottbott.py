import os
import discord
import io
import asyncio
import aiohttp
import PIL.Image
from discord.ext import commands
from dotenv import load_dotenv
from google import genai
from google.genai import types
from memory import (
    init_db, add_user_fact, get_user_facts, add_memory, delete_memory, build_memory_context,
    save_channel_messages, load_channel_messages, prune_channel_history, MessageWithMeta
)

# Load environment variables once at startup
load_dotenv(override=True)

# Configuration
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
CREATOR_ID = int(os.getenv("CREATOR_DISCORD_USER_ID"))
BOT_ID = 1428715040999084133  # The bot's own Discord user ID
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# File processing limits
MAX_FILE_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB maximum file size
MAX_TEXT_DISPLAY_CHARS = 8000  # Characters to display in context

# Model Settings
NIM_MODEL = os.getenv("NIM_MODEL")
IMAGE_MODEL = os.getenv("IMAGE_GENERATION_MODEL")
SONG_MODEL_CLIP = os.getenv("SONG_GENERATION_MODEL_CLIP", "lyria-3-clip-preview")
SONG_MODEL_PRO  = os.getenv("SONG_GENERATION_MODEL_PRO",  "lyria-3-pro-preview")

# Params
SCOTT_TEMP = float(os.getenv("SCOTT_TEMPERATURE"))
CONTEXT_LIMIT = int(os.getenv("CHANNEL_CONTEXT_MESSAGES"))
SAFETY_SETTINGS = os.getenv("SAFETY_SETTINGS", "BLOCK_NONE")

def get_system_prompt(user_id: int = None, guild_id: int = None, user_name: str = None):
    """Build system prompt with personality from environment variables."""
    bot_name = os.getenv("BOT_PERSONALITY_NAME")
    bot_traits = os.getenv("BOT_PERSONALITY_TRAITS")
    bot_tone = os.getenv("BOT_PERSONALITY_TONE")
    bot_backstory = os.getenv("BOT_PERSONALITY_BACKSTORY")
    bot_style = os.getenv("BOT_PERSONALITY_LANGUAGE_STYLE")

    # Build memory context
    memory_context = build_memory_context(user_id, guild_id)
    memory_section = f"\n\n[MEMORY CONTEXT - Use this information if relevant]\n{memory_context}" if memory_context else ""

    # User identification section
    user_section = ""
    if user_name:
        user_section = (
            f"\n\n[CURRENT SPEAKER — STRICT]\n"
            f"You are responding to ONE person right now: **{user_name}** (User ID: {user_id}).\n"
            f"Their message is the LAST message in the conversation, prefixed with `[{user_name}]:`.\n"
            f"\n"
            f"HARD RULES (do not violate):\n"
            f"1. Address ONLY {user_name}. If you use a name, it must be {user_name}.\n"
            f"2. Earlier messages from OTHER users (with different `[Name]:` prefixes) are CONTEXT only — do NOT reply to them, do NOT mix up their names with {user_name}'s.\n"
            f"3. Facts/preferences in [MEMORY CONTEXT] belong to {user_name} ONLY. Never apply them to anyone else.\n"
            f"4. If two messages arrived close together from different people, this prompt is for {user_name} — ignore other speakers entirely."
        )

    return f"""You are {bot_name}, a Discord bot with the following personality:

IMPORTANT IDENTITY INFORMATION:
- Your Discord User ID is: {BOT_ID} - this is how you recognize yourself in mentions and conversations.
- The creator of this bot has User ID: {CREATOR_ID} - treat them specially, refer to them as "creator" or "boss".
- In the conversation history, user messages are prefixed with "[Username]: " so you can tell who is speaking.
- DIFFERENT users have DIFFERENT [Username]: prefixes. Their facts, names, and preferences DO NOT cross over.
- When you see <@{BOT_ID}> or your own name mentioned, you know people are talking about/to you.

Traits: {bot_traits}
Tone: {bot_tone}
Backstory: {bot_backstory}
Language Style: {bot_style}

You have access to real-time web search. When asked about current events, news, weather, sports scores, or any time-sensitive information, always use your search capability to provide accurate, up-to-date answers.

Stay in character at all times. Respond naturally and concisely (Discord has a 2000 character limit). If asked who you are, describe yourself using these traits.{user_section}{memory_section}"""

# Initialize New GenAI Client
# Using v1alpha for Live features, but standard models work here too
client = genai.Client(api_key=GOOGLE_API_KEY, http_options={'api_version': 'v1alpha'})

# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Persistent conversation manager with in-memory LRU cache
class PersistentConversationManager:
    """
    Hybrid conversation storage: SQLite for persistence, dict for hot cache.
    - Survives bot restarts (loads from DB on first access)
    - Memory-efficient (LRU eviction, saves to DB before eviction)
    - Async auto-save for durability without blocking
    """
    def __init__(self, cache_size: int = 20, context_limit: int = 50, auto_save_interval: int = 60):
        self._cache = {}  # channel_id -> list of MessageWithMeta
        self._cache_size = cache_size
        self._context_limit = context_limit
        self._access_order = []  # LRU tracking: oldest first
        self._lock = asyncio.Lock()
        self._auto_save_interval = auto_save_interval
        self._pending_saves = set()  # Track dirty channels
        self._initialized = False
    
    async def _auto_save_loop(self):
        """Background task to periodically save dirty channels."""
        while True:
            await asyncio.sleep(self._auto_save_interval)
            await self._flush_pending_saves()
    
    async def _flush_pending_saves(self):
        """Save all pending channels to SQLite."""
        async with self._lock:
            pending = list(self._pending_saves)
            self._pending_saves.clear()
        
        for channel_id in pending:
            if channel_id in self._cache:
                save_channel_messages(channel_id, self._cache[channel_id])
                print(f"Auto-saved conversation history for channel {channel_id}")
    
    def _mark_dirty(self, channel_id: int):
        """Mark a channel as needing save."""
        self._pending_saves.add(channel_id)
    
    async def _evict_oldest(self):
        """Evict oldest channel from cache, saving to DB first."""
        if not self._access_order:
            return
        
        oldest = self._access_order.pop(0)
        if oldest in self._cache:
            # Save before eviction
            save_channel_messages(oldest, self._cache[oldest])
            print(f"Evicted and saved channel {oldest} from cache")
            del self._cache[oldest]
        self._pending_saves.discard(oldest)
    
    async def get_context(self, channel_id: int) -> list:
        """Get conversation context, loading from DB if not in cache.
        Returns list of types.Content for the AI."""
        async with self._lock:
            if channel_id in self._cache:
                # Move to end (most recently used)
                if channel_id in self._access_order:
                    self._access_order.remove(channel_id)
                self._access_order.append(channel_id)
                # Return just the Content objects for the AI
                return [m.content for m in self._cache[channel_id]]
            
            # Load from SQLite - returns MessageWithMeta objects
            messages = load_channel_messages(channel_id, self._context_limit)
            
            # Evict if at capacity
            if len(self._cache) >= self._cache_size:
                await self._evict_oldest()
            
            self._cache[channel_id] = messages
            self._access_order.append(channel_id)
            print(f"Loaded conversation history for channel {channel_id} ({len(messages)} messages)")
            # Return just the Content objects for the AI
            return [m.content for m in messages]
    
    async def append_message(self, channel_id: int, content, user_id: int = None, user_name: str = None):
        """Add a message to the conversation context."""
        async with self._lock:
            if channel_id not in self._cache:
                # Load first if not cached
                messages = load_channel_messages(channel_id, self._context_limit)
                if len(self._cache) >= self._cache_size:
                    await self._evict_oldest()
                self._cache[channel_id] = messages
                self._access_order.append(channel_id)
            
            # Wrap the Content with metadata
            message = MessageWithMeta(content, user_id=user_id, user_name=user_name)
            
            self._cache[channel_id].append(message)
            
            # Trim to limit
            if len(self._cache[channel_id]) > self._context_limit:
                self._cache[channel_id] = self._cache[channel_id][-self._context_limit:]
            
            self._mark_dirty(channel_id)
    
    async def shutdown(self):
        """Save all pending changes on bot shutdown."""
        print("Shutting down conversation manager, saving all pending channels...")
        await self._flush_pending_saves()
        # Save remaining channels
        async with self._lock:
            for channel_id, messages in self._cache.items():
                save_channel_messages(channel_id, messages)
        print("All conversation history saved.")


# Global conversation manager instance
conversation_mgr = PersistentConversationManager(
    cache_size=20,      # Keep 20 active channels in memory
    context_limit=50,   # Match CONTEXT_LIMIT from env
    auto_save_interval=60  # Auto-save every 60 seconds
)

# --- UTILS ---
async def get_multimodal_content(message):
    """Processes message text and attachments for the new GenAI SDK."""
    parts = []
    if message.content:
        parts.append(message.content)

    if message.attachments:
        async with aiohttp.ClientSession() as session:
            for attachment in message.attachments:
                filename = attachment.filename.lower()
                # Handle images
                if any(filename.endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp']):
                    try:
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                img_data = await resp.read()
                                try:
                                    img = PIL.Image.open(io.BytesIO(img_data))
                                    parts.append(img)
                                except PIL.UnidentifiedImageError as e:
                                    print(f"Error: Corrupted or unsupported image format for {attachment.filename}: {e}")
                                except PIL.Image.DecompressionBombError as e:
                                    print(f"Error: Image too large for {attachment.filename}: {e}")
                    except aiohttp.ClientError as e:
                        print(f"Network error downloading image {attachment.filename}: {e}")
                # Handle text files with streaming and size limits
                elif any(filename.endswith(ext) for ext in ['.txt', '.py', '.md', '.json', '.csv', '.xml', '.html', '.css', '.js', '.ts', '.cpp', '.c', '.h', '.java', '.rb', '.go', '.rs', '.php', '.sql', '.yaml', '.yml', '.log', '.ini', '.cfg', '.sh', '.bat', '.ps1']):
                    try:
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                # Check Content-Length header if available
                                content_length = resp.headers.get('Content-Length')
                                if content_length and int(content_length) > MAX_FILE_SIZE_BYTES:
                                    parts.append(f"\n[File: {attachment.filename} - skipped (too large, {int(content_length):,} bytes)]")
                                    continue
                                # Stream content with size limit to prevent OOM
                                chunks = []
                                total_size = 0
                                async for chunk in resp.content.iter_chunked(8192):
                                    total_size += len(chunk)
                                    if total_size > MAX_FILE_SIZE_BYTES:
                                        parts.append(f"\n[File: {attachment.filename} - skipped (exceeds size limit during download)]")
                                        break
                                    chunks.append(chunk)
                                else:
                                    # Only process if we didn't break due to size limit
                                    try:
                                        text_content = b''.join(chunks).decode('utf-8', errors='replace')
                                        file_info = f"\n\n[File: {attachment.filename}]\n```\n{text_content[:MAX_TEXT_DISPLAY_CHARS]}\n```"
                                        if len(text_content) > MAX_TEXT_DISPLAY_CHARS:
                                            file_info += f"\n...(truncated, total size: {len(text_content):,} chars)"
                                        parts.append(file_info)
                                    except Exception as e:
                                        print(f"Error processing text file {attachment.filename}: {e}")
                    except aiohttp.ClientError as e:
                        print(f"Network error downloading text file {attachment.filename}: {e}")
                # Handle PDFs and other documents (just mention them)
                elif filename.endswith('.pdf'):
                    parts.append(f"\n[Attached PDF file: {attachment.filename} - PDF content cannot be read directly]")
                else:
                    parts.append(f"\n[Attached file: {attachment.filename}]")

    return parts if parts else ["[File uploaded with no text content]"]


# --- COMMANDS ---
@bot.command()
async def scott(ctx, *, arg=None):
    if not arg:
        await ctx.send(
            "Scott is here. Try:\n"
            "• `!scott image [prompt]` — generate an image\n"
            "• `!scott song [prompt]` — generate a 30-second music clip (fast)\n"
            "• `!scott song long [prompt]` — generate a full song with vocals (slow)\n"
            "• `!scott remember [text]` / `!scott forget [text]` / `!scott facts [@user]` — memory"
        )
        return

    arg_lower = arg.strip().lower()
    if arg_lower.startswith("image "):
        prompt = arg[6:]
        
        # Image generation failsafe - refuse explicit/inappropriate requests
        explicit_keywords = [
            'nude', 'naked', 'sex', 'sexual', 'porn', 'explicit', 'nsfw',
            'erotic', 'intimate', 'breasts', 'genitals', 'buttocks', 'ass',
            'fuck', 'fucking', 'intercourse', 'orgasm', 'masturbation',
            'underwear', 'lingerie', 'thong', 'bikini', 'revealing',
            'suggestive', 'provocative', 'seductive', 'adult content'
        ]
        
        prompt_lower = prompt.lower()
        if any(keyword in prompt_lower for keyword in explicit_keywords):
            await ctx.send("I cannot generate explicit or inappropriate images. Please try a different request.")
            return
        
        async with ctx.typing():
            try:
                # Using the new SDK for image generation (Imagen)
                response = await client.aio.models.generate_content(
                    model=IMAGE_MODEL,
                    contents=f"Generate image: {prompt}",
                    config=types.GenerateContentConfig(
                        safety_settings=[types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")]
                    )
                )

                # Extract image bytes from the first candidate
                for part in response.candidates[0].content.parts:
                    if part.inline_data:
                        await ctx.reply(file=discord.File(io.BytesIO(part.inline_data.data), "scott_art.png"))
                        return
                await ctx.send("Couldn't paint that one, sorry.")
            except Exception as e:
                await ctx.send(f"Image Error: {e}")
    elif arg_lower.startswith("song"):
        # !scott song [prompt]            -> 30-second clip via Lyria 3 Clip (faster)
        # !scott song long [prompt]       -> full song via Lyria 3 Pro (slower, has vocals/lyrics)
        rest = arg[4:].strip()  # everything after "song"
        if rest.lower().startswith("long"):
            prompt = rest[4:].strip()
            song_model = SONG_MODEL_PRO
            song_label = "full song"
            file_ext = "wav"  # Lyria 3 Pro can output WAV when configured for AUDIO modality
        else:
            prompt = rest
            song_model = SONG_MODEL_CLIP
            song_label = "30-sec clip"
            file_ext = "mp3"

        if not prompt:
            await ctx.send(
                "Usage: `!scott song [prompt]` for a 30-second clip, "
                "or `!scott song long [prompt]` for a full song with vocals."
            )
            return

        notice = await ctx.send(f"🎵 Composing a {song_label}... this can take 30-90 seconds.")

        try:
            # Both Lyria 3 Clip and Pro return audio reliably when AUDIO is
            # explicitly requested as a response modality. Without it the SDK
            # sometimes silently returns text-only refusals or commentary.
            gen_config = types.GenerateContentConfig(
                response_modalities=["AUDIO", "TEXT"],
            )

            async with ctx.typing():
                response = await client.aio.models.generate_content(
                    model=song_model,
                    contents=prompt,
                    config=gen_config,
                )

            # Walk the response looking for audio bytes in inline_data.
            # Lyria 3 puts parts in either response.parts (direct) or
            # response.candidates[0].content.parts (Gemini-style); handle both.
            audio_bytes = None
            text_parts = []

            # Path 1: response.parts (Lyria docs example shape)
            try:
                if getattr(response, "parts", None):
                    for part in response.parts:
                        data = getattr(getattr(part, "inline_data", None), "data", None)
                        if data and not audio_bytes:
                            audio_bytes = data
                        txt = getattr(part, "text", None)
                        if txt:
                            text_parts.append(txt)
            except (AttributeError, TypeError, IndexError) as walk_err:
                print(f"[song] response.parts walk failed: {walk_err}")

            # Path 2: response.candidates[0].content.parts (Gemini-style)
            if not audio_bytes:
                try:
                    cands = getattr(response, "candidates", None)
                    if cands:
                        for cand in cands:
                            content = getattr(cand, "content", None)
                            if not content:
                                continue
                            parts = getattr(content, "parts", None) or []
                            for part in parts:
                                data = getattr(getattr(part, "inline_data", None), "data", None)
                                if data and not audio_bytes:
                                    audio_bytes = data
                                txt = getattr(part, "text", None)
                                if txt and txt not in text_parts:
                                    text_parts.append(txt)
                            if audio_bytes:
                                break
                except (AttributeError, TypeError, IndexError) as walk_err:
                    print(f"[song] response.candidates walk failed: {walk_err}")

            if not audio_bytes:
                # No audio bytes anywhere. Three likely reasons:
                # 1. The model refused (returned text instead) — content filter
                # 2. The model isn't actually allowlisted on this API key
                # 3. The SDK shape changed and we missed where audio lives
                # Dump everything we can see so we can diagnose.
                model_text = "".join(text_parts).strip() if text_parts else ""
                print(f"[song] No audio found. Response type: {type(response).__name__}")
                print(f"[song] Response attrs: {[a for a in dir(response) if not a.startswith('_')][:20]}")
                if model_text:
                    print(f"[song] Model text response: {model_text[:1000]}")
                # Also try to print prompt feedback / finish reason for content-filter cases
                try:
                    pf = getattr(response, "prompt_feedback", None)
                    if pf:
                        print(f"[song] prompt_feedback: {pf}")
                    cands = getattr(response, "candidates", None) or []
                    for i, c in enumerate(cands):
                        fr = getattr(c, "finish_reason", None)
                        sr = getattr(c, "safety_ratings", None)
                        print(f"[song] candidate[{i}] finish_reason={fr} safety_ratings={sr}")
                except Exception:
                    pass

                if model_text:
                    msg = f"❌ The model didn't generate audio. It said:\n> {model_text[:500]}"
                else:
                    msg = ("❌ The model returned no audio and no text. "
                           "Most likely Lyria 3 isn't enabled on your project, "
                           "or your prompt triggered a content filter. "
                           "Check the bot console for `[song]` diagnostic output.")
                await notice.edit(content=msg)
                return

            # Sanitize prompt into a safe filename stub
            import re as _re
            stub = _re.sub(r"[^a-zA-Z0-9_-]+", "_", prompt[:40]).strip("_") or "song"
            filename = f"scottbott_{stub}.{file_ext}"

            file_obj = discord.File(io.BytesIO(audio_bytes), filename)
            commentary = "".join(text_parts).strip() if text_parts else ""
            content = f"🎵 **{song_label}**: {prompt[:200]}"
            if commentary:
                content += f"\n> {commentary[:300]}"
            await ctx.reply(content=content, file=file_obj)
            try:
                await notice.delete()
            except Exception:
                pass
        except Exception as e:
            err_text = str(e)
            print(f"[song] Error: {type(e).__name__}: {err_text}")
            import traceback
            traceback.print_exc()
            # Surface common configuration problems
            if "PERMISSION_DENIED" in err_text or "403" in err_text:
                msg = (f"❌ Lyria 3 isn't enabled on your project, or your API key lacks access. "
                       f"Enable music generation at https://aistudio.google.com/")
            elif "NOT_FOUND" in err_text or "model" in err_text.lower() and "not found" in err_text.lower():
                msg = (f"❌ Model `{song_model}` not available. Lyria 3 may need allowlist access "
                       f"on your account. Check https://ai.google.dev/gemini-api/docs/music-generation")
            elif "RESOURCE_EXHAUSTED" in err_text or "429" in err_text:
                msg = "❌ Rate limited or quota exhausted on music generation."
            else:
                msg = f"❌ Song Error: {type(e).__name__}: {err_text[:300]}"
            try:
                await notice.edit(content=msg)
            except Exception:
                await ctx.send(msg)
    elif arg_lower.startswith("remember "):
        memory_text = arg[9:].strip()
        if not memory_text:
            await ctx.send("Usage: `!scott remember [something to remember]`")
            return
        add_memory(
            content=memory_text,
            user_id=ctx.author.id,
            user_name=ctx.author.display_name,
            guild_id=ctx.guild.id if ctx.guild else None,
            importance=2
        )
        await ctx.send("Got it. I'll remember that.")
    elif arg_lower.startswith("forget "):
        memory_text = arg[7:].strip()
        if not memory_text:
            await ctx.send("Usage: `!scott forget [exact text to forget]`")
            return
        deleted = delete_memory(ctx.author.id, memory_text)
        if deleted > 0:
            await ctx.send("Forgotten.")
        else:
            await ctx.send("Couldn't find that memory.")
    elif arg_lower.startswith("facts"):
        # Handle !scott facts or !scott facts @user
        parts = arg.split()
        member = None
        if len(parts) > 1:
            # Try to get member from mention
            for word in parts[1:]:
                if word.startswith('<@') and word.endswith('>'):
                    try:
                        member_id = int(word.strip('<@!>'))
                        member = ctx.guild.get_member(member_id)
                    except (ValueError, AttributeError):
                        pass
        target = member or ctx.author
        facts_list = get_user_facts(target.id, ctx.guild.id if ctx.guild else None)
        if not facts_list:
            await ctx.send(f"I don't know anything about {target.display_name} yet.")
            return
        fact_lines = [f"• [{cat}] {fact}" for fact, cat, user_name in facts_list]
        await ctx.send(f"**What I know about {target.display_name}:**\n" + "\n".join(fact_lines))
    else:
        await ctx.send("Unknown command.")


@bot.event
async def on_message(message):
    if message.author == bot.user: return
    await bot.process_commands(message)

    is_mentioned = bot.user.mentioned_in(message)
    is_reply = False
    if message.reference:
        try:
            replied_to = await message.channel.fetch_message(message.reference.message_id)
            is_reply = replied_to.author == bot.user
        except:
            pass
    
    if is_mentioned or is_reply:
        channel_id = message.channel.id
        channel_context = await conversation_mgr.get_context(channel_id)
            
        async with message.channel.typing():
            try:
                content_parts = await get_multimodal_content(message)
                user_name = message.author.display_name

                # Build parts for STORAGE (raw, no prefix - the persistent layer adds it).
                storage_parts = []
                # Build parts for the LIVE call. Prepend [Username]: to the first text
                # part so the AI sees the same attribution format the saved history uses.
                # Without this, the live message arrives unprefixed and the model can
                # confuse who is currently speaking when multiple users hit the bot
                # at once.
                live_parts = []
                first_text_done = False
                for p in content_parts:
                    if isinstance(p, str):
                        storage_parts.append(types.Part(text=p))
                        if not first_text_done:
                            live_parts.append(types.Part(text=f"[{user_name}]: {p}"))
                            first_text_done = True
                        else:
                            live_parts.append(types.Part(text=p))
                    else:
                        # Convert PIL Image to bytes
                        img_bytes = io.BytesIO()
                        p.save(img_bytes, format='PNG')
                        img_bytes.seek(0)
                        blob = types.Blob(data=img_bytes.getvalue(), mime_type="image/png")
                        storage_parts.append(types.Part(inline_data=blob))
                        live_parts.append(types.Part(inline_data=blob))
                # If the message had no text content at all, still include attribution
                if not first_text_done:
                    live_parts.insert(0, types.Part(text=f"[{user_name}]: "))

                # Persist the message (storage layer prepends [Username]: itself)
                user_content = types.Content(role="user", parts=storage_parts)
                await conversation_mgr.append_message(channel_id, user_content, user_id=message.author.id, user_name=user_name)

                # Build full context for the AI: history (with [Username] prefixes
                # already on past messages) + the current message ALSO prefixed.
                live_user_content = types.Content(role="user", parts=live_parts)
                full_context = channel_context + [live_user_content]
                
                # New SDK generate_content call with web search enabled
                # (user_name was set above when building parts)
                response = await client.aio.models.generate_content(
                    model=NIM_MODEL,
                    contents=full_context,
                    config=types.GenerateContentConfig(
                        system_instruction=get_system_prompt(message.author.id, message.guild.id if message.guild else None, user_name),
                        temperature=SCOTT_TEMP,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        safety_settings=[types.SafetySetting(category="HARM_CATEGORY_HARASSMENT", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_HATE_SPEECH", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_SEXUALLY_EXPLICIT", threshold="BLOCK_NONE"),
                                       types.SafetySetting(category="HARM_CATEGORY_DANGEROUS_CONTENT", threshold="BLOCK_NONE")]
                    )
                )

                # Defensive checks for response structure
                model_text = None
                if response and hasattr(response, 'text') and response.text:
                    model_text = response.text
                elif response and hasattr(response, 'candidates') and response.candidates:
                    for candidate in response.candidates:
                        if hasattr(candidate, 'content') and candidate.content:
                            if hasattr(candidate.content, 'parts') and candidate.content.parts:
                                for part in candidate.content.parts:
                                    if hasattr(part, 'text') and part.text:
                                        model_text = part.text
                                        break
                        if model_text:
                            break

                if not model_text:
                    model_text = "I couldn't process that request."

                # Add model response to persistent conversation (model has no user)
                model_content = types.Content(role="model", parts=[types.Part(text=model_text)])
                await conversation_mgr.append_message(channel_id, model_content, user_id=None, user_name=None)
                
                # Auto-extract and store facts if the AI mentions learning something
                # Simple heuristic: look for patterns in the conversation to extract facts
                if "my name is" in message.content.lower() or "i am" in message.content.lower() or "i like" in message.content.lower():
                    # Store the message content as a potential fact
                    add_user_fact(
                        user_id=message.author.id,
                        user_name=message.author.display_name,
                        fact=message.content,
                        guild_id=message.guild.id if message.guild else None,
                        category="user_statement"
                    )
                
                await message.reply(model_text[:2000])
            except Exception as e:
                import traceback
                print(f"Chat Error: {type(e).__name__}: {e}")
                print(f"Message content: {repr(message.content)}")
                print(f"Attachments: {[a.filename for a in message.attachments]}")
                traceback.print_exc()

@bot.event
async def on_ready():
    init_db()
    # Start auto-save background task
    bot.loop.create_task(conversation_mgr._auto_save_loop())

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"Conversation manager active: {conversation_mgr._cache_size} channel cache, auto-save every {conversation_mgr._auto_save_interval}s")


@bot.event
async def on_disconnect():
    """Save all conversation history on disconnect."""
    await conversation_mgr.shutdown()


def run_bot():
    """Run the bot with graceful shutdown handling."""
    import signal
    
    def signal_handler(sig, frame):
        print("\nShutdown signal received, saving conversation history...")
        # Schedule shutdown in the event loop
        asyncio.create_task(conversation_mgr.shutdown())
        bot.loop.stop()
    
    # Register signal handlers (Unix/Windows compatible)
    try:
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except (AttributeError, ValueError):
        pass  # Windows doesn't have SIGTERM, or signals not available
    
    bot.run(DISCORD_BOT_TOKEN)


run_bot()