import io
import re
import traceback
import discord
from discord.ext import commands
from google.genai import types

from config import DISCORD_BOT_TOKEN, NIM_MODEL
from memory import init_db, get_user_notes
from conversation import PersistentConversationManager, _maybe_extract_fact
from security import get_multimodal_content
from prompts import get_system_prompt
from services.ai import generate_chat_with_nim
from services.search import web_search
from ui import NotesEditModal
from commands.scott import handle_scott


NO_SEARCH_PHRASES = [
    "ill stay", "i'll stay", "enjoying", "company", "you keep",
    "calling me", "why do you", "how are you", "im good", "i'm good",
    "lol", "haha", "thanks", "thank you", "okay", "ok", "sure",
    "yes", "no", "maybe", "nice", "cool", "great", "wow"
]

SEARCH_SIGNALS = [
    "?", "what is", "who is", "when did", "where is", "how does",
    "latest", "news", "price", "weather", "score", "define",
    "search for", "look up", "find me", "current", "today"
]

def sanitize_model_output(text: str) -> str:
    """Strip any input scaffolding the model may have echoed back.

    Removes leaked SPEAKER CONTEXT blocks, [Name] (ID:123): prefixes,
    and [Reply context ...] lines so they never reach the channel.
    """
    if not text:
        return text

    # Remove a full --- SPEAKER CONTEXT --- ... --- END SPEAKER CONTEXT --- block
    text = re.sub(
        r"-{2,}\s*SPEAKER CONTEXT\s*-{2,}.*?-{2,}\s*END SPEAKER CONTEXT\s*-{2,}",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove any dangling SPEAKER CONTEXT key=value lines if the block was partial
    text = re.sub(
        r"^\s*(ACTIVE_USER_ID|ACTIVE_USERNAME|REPLY_ONLY_TO|IGNORE_OTHER_NAMES|"
        r"REPLIED_TO_USER_ID|REPLIED_TO_USERNAME)\s*=.*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    # Remove echoed/fabricated [WEB SEARCH RESULTS ...] ... [END SEARCH RESULTS] blocks
    text = re.sub(
        r"\[WEB SEARCH RESULTS.*?(?:\[END SEARCH RESULTS\]|\Z)",
        "",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Remove a stray standalone [WEB SEARCH RESULTS] marker line
    text = re.sub(r"^\s*\[WEB SEARCH RESULTS.*?\]\s*$", "", text, flags=re.MULTILINE)

    # Remove echoed [Reply context — ...] lines
    text = re.sub(r"^\s*\[Reply context.*?\]\s*$", "", text, flags=re.MULTILINE)
    # Remove an echoed leading speaker prefix like "[Name] (ID:123): "
    text = re.sub(r"^\s*\[[^\]]+\]\s*\(ID:\d+\):\s*", "", text)

    return text.strip()


def needs_search(text: str) -> bool:
    if not text:
        return False
    
    # Strip mentions and clean
    clean = re.sub(r"<@!?\d+>", "", text).strip().lower()
    
    # Too short to need search
    if len(clean.split()) <= 4 and "?" not in clean:
        return False
    
    # Conversational phrases — never search
    if any(phrase in clean for phrase in NO_SEARCH_PHRASES):
        return False
    
    # Only search if explicit signal present
    if any(signal in clean for signal in SEARCH_SIGNALS):
        return True
    
    return False  # default NO search


# Discord Bot Setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Conversation manager
conversation_mgr = PersistentConversationManager(
    cache_size=20,
    context_limit=50,
    auto_save_interval=60,
)


def resolve_member_query(guild, query: str):
    """Resolve a member query to find matching guild members."""
    print(f"[WARNING] resolve_member_query called with query: '{query}' - this may cause performance issues if called on every message")
    if not guild:
        return None
    query_clean = query.lower().strip().lstrip("@")
    matches = [
        m for m in guild.members
        if query_clean in m.display_name.lower()
        or query_clean in m.name.lower()
    ]
    if not matches:
        return f"No member found matching '{query}' in this server."
    if len(matches) == 1:
        m = matches[0]
        roles = [r.name for r in m.roles if r.name != "@everyone"]
        return (f"Member found: {m.display_name} (username: {m.name}, "
                f"ID: {m.id}, roles: {', '.join(roles) or 'none'}, "
                f"joined: {m.joined_at.strftime('%d %b %Y') if m.joined_at else 'unknown'})")
    return f"Multiple matches: {', '.join(m.display_name for m in matches[:5])}"


@bot.command()
async def scott(ctx, *, arg=None):
    await handle_scott(ctx, arg, conversation_mgr)


@bot.tree.command(name="editnotes", description="Edit your personal notes — only you can see the result")
async def slash_editnotes(interaction: discord.Interaction):
    guild_id = interaction.guild_id
    current_notes = get_user_notes(interaction.user.id, guild_id)
    modal = NotesEditModal(interaction.user.id, guild_id, current_notes)
    await interaction.response.send_modal(modal)


@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.content.strip().lower().startswith("!scott"):
        await bot.process_commands(message)
        return

    is_mentioned = bot.user.mentioned_in(message)
    is_reply = False
    replied_to_message = None
    if message.reference:
        try:
            replied_to_message = await message.channel.fetch_message(message.reference.message_id)
            is_reply = replied_to_message.author == bot.user
        except Exception:
            pass

    if not (is_mentioned or is_reply):
        return

    channel_id = message.channel.id
    channel_context = await conversation_mgr.get_context(channel_id)

    async with message.channel.typing():
        try:
            content_parts = await get_multimodal_content(message)
            if message.reference and replied_to_message and replied_to_message.attachments:
                ref_parts = await get_multimodal_content(replied_to_message)
                for p in ref_parts:
                    if not isinstance(p, str):
                        content_parts.append(p)

            user_name = message.author.display_name

            user_message_text = ""
            storage_parts = []
            for p in content_parts:
                if isinstance(p, str):
                    user_message_text += p + "\n"
                    storage_parts.append(types.Part(text=p))
                else:
                    storage_parts.append(types.Part(text="[image attachment]"))
            user_message_text = user_message_text.strip()

            user_content = types.Content(role="user", parts=storage_parts)
            await conversation_mgr.append_message(
                channel_id, user_content, user_id=message.author.id, user_name=user_name
            )

            system_prompt = get_system_prompt(
                message.author.id,
                message.guild.id if message.guild else None,
                user_name,
            )

            # Build focused history slice: keep full DB context for storage,
            # but send only a small recent window to the model to reduce
            # cross-user contamination.
            FOCUSED_HISTORY_LIMIT = 10
            focused_history = list(channel_context)
            if len(focused_history) > FOCUSED_HISTORY_LIMIT:
                focused_history = focused_history[-FOCUSED_HISTORY_LIMIT:]

            # Collect other speaker names from the focused history for isolation.
            _other_names = set()
            for msg in focused_history:
                if not (hasattr(msg, 'parts') and msg.parts):
                    continue
                for part in msg.parts:
                    text = getattr(part, 'text', '') or ''
                    if not text.startswith('['):
                        continue
                    # Handle old format [Name]: and new format [Name] (ID:123):
                    end = text.find(']: ')
                    if end != -1:
                        name = text[1:end]
                    else:
                        close = text.find(']')
                        if close != -1 and text.find('] (ID:', close) == close:
                            name = text[1:close]
                        else:
                            continue
                    if name and name != user_name:
                        _other_names.add(name)
            ignore_names = ', '.join(sorted(_other_names)) if _other_names else 'none'

            # Structured speaker metadata block — hard metadata the model cannot miss.
            # Placed at the very end of context, right before the latest user turn.
            speaker_lines = [
                "--- SPEAKER CONTEXT ---",
                f"ACTIVE_USER_ID={message.author.id}",
                f"ACTIVE_USERNAME={user_name}",
                f"REPLY_ONLY_TO={user_name}",
                f"IGNORE_OTHER_NAMES={ignore_names}",
            ]
            if replied_to_message and replied_to_message.author.id != message.author.id:
                speaker_lines.append(
                    f"REPLIED_TO_USER_ID={replied_to_message.author.id}"
                )
                speaker_lines.append(
                    f"REPLIED_TO_USERNAME={replied_to_message.author.display_name}"
                )
            speaker_lines.append("--- END SPEAKER CONTEXT ---")
            speaker_block = "\n".join(speaker_lines) + "\n"

            chat_user_message = ""
            if replied_to_message and replied_to_message.content:
                reply_author = replied_to_message.author.display_name
                chat_user_message += (
                    f"[Reply context — replying to message by {reply_author}: "
                    f'"{replied_to_message.content}"]\n'
                )
            chat_user_message += (
                f"[{user_name}] (ID:{message.author.id}): {user_message_text}"
                if user_message_text
                else f"[{user_name}] (ID:{message.author.id}): "
            )
            search_context = ""
            if needs_search(user_message_text):
                search_query = user_message_text[:200]
                search_context = await web_search(search_query)
                if search_context:
                    print("[DEBUG] Injecting web search results into context")

            augmented_system = system_prompt + "\n\n" + speaker_block
            if search_context:
                augmented_system += (
                    "\n\n[WEB SEARCH RESULTS — these are LIVE results fetched right now. "
                    "Use them to answer. Do NOT say you lack internet access or cannot search.]\n"
                    f"{search_context}\n[END SEARCH RESULTS]"
                )

            print(f"[DEBUG] Using NIM chat model: {NIM_MODEL}")
            model_text = await generate_chat_with_nim(
                system_prompt=augmented_system,
                user_message=chat_user_message,
                conversation_history=focused_history,
            )

            if model_text is None:
                await message.reply(
                    "❌ Sorry, I couldn't generate a response. "
                    "The AI service might be temporarily unavailable."
                )
                return

            # One-pass validator: if the reply names a wrong recent user, rerun once.
            if _other_names:
                lower_response = model_text.lower()
                wrong_used = set()
                for wrong_name in _other_names:
                    pattern = r'(?<!\w)' + re.escape(wrong_name.lower()) + r'(?!\w)'
                    if re.search(pattern, lower_response):
                        wrong_used.add(wrong_name)
                if wrong_used:
                    print(
                        f"[VALIDATOR] Response used wrong name(s): {wrong_used}. "
                        f"Rerunning with correction instruction."
                    )
                    correction = (
                        "\n\n[CRITICAL CORRECTION — previous response was wrong]\n"
                        f"You addressed the wrong user. You MUST ONLY reply to "
                        f"{user_name} (ID:{message.author.id}). "
                        f"Never mention or address: {', '.join(sorted(wrong_used))}. "
                        "Regenerate your response correctly."
                    )
                    model_text = await generate_chat_with_nim(
                        system_prompt=augmented_system + correction,
                        user_message=chat_user_message,
                        conversation_history=focused_history,
                    )
                    if model_text is None:
                        await message.reply(
                            "❌ Sorry, I couldn't generate a response. "
                            "The AI service might be temporarily unavailable."
                        )
                        return

            # Strip any input scaffolding the model echoed back before storing/sending.
            model_text = sanitize_model_output(model_text)

            model_content = types.Content(role="model", parts=[types.Part(text=model_text)])
            await conversation_mgr.append_message(channel_id, model_content, user_id=None, user_name=None)

            fact_extracted = await _maybe_extract_fact(message)
            if fact_extracted:
                print(f"[FACT] Auto-extracted fact from {message.author.display_name}")

            if len(model_text) > 2000:
                file_bytes = io.BytesIO(model_text.encode("utf-8"))
                await message.reply(
                    "Response was too long — here's a file:",
                    file=discord.File(file_bytes, filename="response.txt")
                )
            else:
                await message.reply(model_text)

        except Exception as e:
            print(f"Chat Error: {type(e).__name__}: {e}")
            print(f"Message content: {repr(message.content)}")
            print(f"Attachments: {[a.filename for a in message.attachments]}")
            traceback.print_exc()


@bot.event
async def on_ready():
    init_db()
    bot.loop.create_task(conversation_mgr._auto_save_loop())
    await bot.tree.sync()
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print(
        f"Conversation manager active: {conversation_mgr._cache_size} channel cache, "
        f"auto-save every {conversation_mgr._auto_save_interval}s"
    )


@bot.event
async def on_disconnect():
    await conversation_mgr.shutdown()


def run_bot():
    bot.run(DISCORD_BOT_TOKEN)


run_bot()
