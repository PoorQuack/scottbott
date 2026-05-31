import io
import discord
from config import CREATOR_ID
from memory import (
    add_memory,
    search_memories,
    delete_by_id,
    get_user_facts,
    get_user_notes,
    get_guild_personality,
)
from services.images import expand_prompt_with_grok, generate_image, generate_image_nvidia
from services.ai import set_reasoning_mode, get_reasoning_mode
from services.voice_session import join_voice, leave_voice, say_text
from services.music import generate_song, format_song_error
from prompts import _build_default_personality_text
from ui import NotesEditView, PersonalityView


_NSFW_TAGS = {
    "nude", "nudity", "naked", "nipple", "nipples", "pussy", "penis", "vagina",
    "sex", "nsfw", "explicit", "hentai", "pornographic", "erection", "cum",
    "vaginal", "anal", "oral", "ahegao", "topless", "bottomless", "genitals",
}


async def handle_scott(ctx, arg, conversation_mgr):
    """Dispatch !scott subcommands."""
    if not arg:
        await ctx.send(
            "Scott is here. Try:\n"
            "• `!scott imagine [prompt]` — generate an anime image\n"
            "• `!scott aigen [prompt]` — generate an image with Stable Diffusion 3.5\n"
            "• `!scott join` — join your voice channel and talk out loud\n"
            "• `!scott leave` — leave the voice channel\n"
            "• `!scott say [text]` — make the bot speak text out loud (TTS test)\n"
            "• `!scott light` — faster, casual replies (low reasoning)\n"
            "• `!scott heavy` — sharper coding/complex help (high reasoning)\n"
            "• `!scott song [prompt]` — generate a 30-second music clip (fast)\n"
            "• `!scott song long [prompt]` — generate a full song with vocals (slow)\n"
            "• `!scott remember [text]` — remember something\n"
            "• `!scott forget [keyword]` — forget matching memories about yourself\n"
            "• `!scott forget @user [keyword]` — forget matching memories about someone else\n"
            "• `!scott facts [@user]` — show what I know about you (or someone else)\n"
            "• `!scott editnotes` — edit your personal notes I always remember"
        )
        return

    arg_lower = arg.strip().lower()
    if arg_lower == "join":
        await ctx.send(await join_voice(ctx.bot, ctx))
    elif arg_lower == "leave":
        await ctx.send(await leave_voice(ctx))
    elif arg_lower.startswith("say "):
        msg = await say_text(ctx, arg[4:].strip())
        if msg:
            await ctx.send(msg)
    elif arg_lower == "light":
        mode = set_reasoning_mode("low")
        await ctx.send(f"⚡ Light mode — reasoning effort set to **{mode}**. Faster, more casual replies.")
    elif arg_lower == "heavy":
        mode = set_reasoning_mode("high")
        await ctx.send(f"🧠 Heavy mode — reasoning effort set to **{mode}**. Slower but sharper for coding and complex stuff.")
    elif arg_lower.startswith("aigen "):
        await _handle_aigen(ctx, arg[6:].strip())
    elif arg_lower.startswith("imagine "):
        await _handle_imagine(ctx, arg[8:])
    elif arg_lower.startswith("song"):
        await _handle_song(ctx, arg[4:].strip())
    elif arg_lower.startswith("remember "):
        await _handle_remember(ctx, arg[9:].strip())
    elif arg_lower.startswith("forget"):
        await _handle_forget(ctx, arg[6:].strip())
    elif arg_lower.startswith("facts"):
        await _handle_facts(ctx, arg)
    elif arg_lower.startswith("editnotes"):
        await _handle_editnotes(ctx)
    elif arg_lower.startswith("editpersonality"):
        await _handle_editpersonality(ctx, conversation_mgr)
    else:
        await ctx.send("Unknown command.")


async def _handle_aigen(ctx, prompt):
    """Generate an image via NVIDIA Stable Diffusion 3.5 Large."""
    if not prompt:
        await ctx.send("Usage: `!scott aigen [prompt]`")
        return

    is_explicit = bool({w.lower() for w in prompt.split()} & _NSFW_TAGS)
    if is_explicit and not getattr(ctx.channel, "nsfw", False):
        await ctx.reply("🔞 Explicit prompts can only be used in an Age-Restricted channel.")
        return

    notice = await ctx.send("🎨 Generating with Stable Diffusion 3.5...")
    async with ctx.typing():
        image_bytes = await generate_image_nvidia(prompt)

    if image_bytes:
        await ctx.reply(file=discord.File(io.BytesIO(image_bytes), filename="aigen.jpeg"))
        try:
            await notice.delete()
        except Exception:
            pass
    else:
        await notice.edit(content="Image generation failed — NVIDIA returned no image. Check the console log.")


async def _handle_imagine(ctx, prompt):
    expanded_prompt = await expand_prompt_with_grok(prompt)

    prompt_tags = {t.strip().lower() for t in expanded_prompt.split(",")}
    is_explicit = bool(prompt_tags & _NSFW_TAGS)
    is_nsfw_channel = getattr(ctx.channel, "nsfw", False)
    if is_explicit and not is_nsfw_channel:
        await ctx.reply("🔞 This image contains explicit content and can only be generated in an Age-Restricted channel.")
        return

    async with ctx.typing():
        image_bytes = await generate_image(prompt, expanded_prompt=expanded_prompt)

    if image_bytes:
        await ctx.reply(file=discord.File(io.BytesIO(image_bytes), filename="imagine.jpeg"))
    else:
        await ctx.send("Image generation failed: Replicate returned no data.")


async def _handle_song(ctx, rest):
    if rest.lower().startswith("long"):
        prompt = rest[4:].strip()
        long_version = True
    else:
        prompt = rest
        long_version = False

    if not prompt:
        await ctx.send(
            "Usage: `!scott song [prompt]` for a 30-second clip, "
            "or `!scott song long [prompt]` for a full song with vocals."
        )
        return

    label = "full song" if long_version else "30-sec clip"
    notice = await ctx.send(f"🎵 Composing a {label}... this can take 30-90 seconds.")

    try:
        async with ctx.typing():
            audio_bytes, text_parts, file_ext, label, filename = await generate_song(prompt, long_version=long_version)

        file_obj = discord.File(io.BytesIO(audio_bytes), filename)
        commentary = "".join(text_parts).strip() if text_parts else ""
        content = f"🎵 **{label}**: {prompt[:200]}"
        if commentary:
            content += f"\n> {commentary[:300]}"
        await ctx.reply(content=content, file=file_obj)
        try:
            await notice.delete()
        except Exception:
            pass
    except Exception as e:
        msg = format_song_error(e)
        try:
            await notice.edit(content=msg)
        except Exception:
            await ctx.send(msg)


async def _handle_remember(ctx, memory_text):
    if not memory_text:
        await ctx.send("Usage: `!scott remember [something to remember]`")
        return
    add_memory(
        content=memory_text,
        user_id=ctx.author.id,
        user_name=ctx.author.display_name,
        guild_id=ctx.guild.id if ctx.guild else None,
        importance=2,
    )
    await ctx.send("Got it. I'll remember that.")


async def _handle_forget(ctx, rest):
    target_member = None
    keyword = rest
    words = rest.split()
    if words and words[0].startswith('<@') and words[0].endswith('>'):
        try:
            member_id = int(words[0].strip('<@!>'))
            target_member = ctx.guild.get_member(member_id) if ctx.guild else None
            keyword = ' '.join(words[1:]).strip()
        except (ValueError, AttributeError):
            pass

    target = target_member or ctx.author

    if not keyword:
        await ctx.send(
            "Usage: `!scott forget [keyword]` — forget things about yourself\n"
            "Or: `!scott forget @user [keyword]` — forget things about someone else"
        )
        return

    matches = search_memories(target.id, keyword, ctx.guild.id if ctx.guild else None)
    if not matches:
        await ctx.send(f"No memories found matching **{keyword}** for {target.display_name}.")
        return

    deleted = 0
    forgotten = []
    for table, row_id, text in matches:
        n = delete_by_id(table, row_id)
        if n > 0:
            deleted += n
            forgotten.append(f"• {text}")

    if deleted > 0:
        lines = "\n".join(forgotten[:10])
        await ctx.send(f"Forgot {deleted} thing(s) about **{target.display_name}**:\n{lines}")
    else:
        await ctx.send("Something went wrong, nothing was deleted.")


async def _handle_facts(ctx, arg):
    parts = arg.split()
    member = None
    if len(parts) > 1:
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


async def _handle_editnotes(ctx):
    guild_id = ctx.guild.id if ctx.guild else None
    current_notes = get_user_notes(ctx.author.id, guild_id)
    view = NotesEditView(ctx.author.id, guild_id, current_notes)
    await ctx.send(
        "Click the button below to open your notes editor. Only you can use it.",
        view=view,
        delete_after=120,
    )


async def _handle_editpersonality(ctx, conversation_mgr):
    if not ctx.guild:
        await ctx.send("This command can only be used in a server.")
        return
    if not ctx.author.guild_permissions.administrator and ctx.author.id != CREATOR_ID:
        await ctx.send("You need administrator permissions to edit the server personality.")
        return
    current = get_guild_personality(ctx.guild.id) or ""
    view = PersonalityView(current, ctx.guild.id, ctx.author.id, conversation_mgr)
    shown = current if current else _build_default_personality_text()
    if len(shown) > 3900:
        shown = shown[:3900] + "\n…(truncated for display)"
    label = "Current override:" if current else "Current (default from .env — click Edit to customise):"
    embed = discord.Embed(
        title="Server Personality",
        description=f"**{label}**\n{shown}",
        color=discord.Color.blurple(),
    )
    await ctx.send(embed=embed, view=view)
