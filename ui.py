import discord
from memory import (
    get_user_notes,
    set_user_notes,
    clear_user_notes,
    set_guild_personality,
    reset_guild_personality,
    get_guild_personality,
)
from prompts import _build_default_personality_text
from config import CREATOR_ID


class NotesEditModal(discord.ui.Modal, title="Edit My Notes"):
    notes_input = discord.ui.TextInput(
        label="Your notes (blank = clear all notes)",
        style=discord.TextStyle.paragraph,
        max_length=4000,
        required=False,
        placeholder="Write anything you want me to remember about you…",
    )

    def __init__(self, user_id: int, guild_id: int, current_notes: str):
        super().__init__()
        self.user_id = user_id
        self.guild_id = guild_id
        self.notes_input.default = current_notes[:4000] if current_notes else ""

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("These are not your notes.", ephemeral=True)
            return
        try:
            text = (self.notes_input.value or "").strip()
            if not text:
                clear_user_notes(self.user_id, self.guild_id)
                await interaction.response.send_message("✅ Notes cleared.", ephemeral=True)
            else:
                set_user_notes(self.user_id, text, self.guild_id)
                await interaction.response.send_message("✅ Notes saved! I'll keep these in mind.", ephemeral=True)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ Error saving notes: {e}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Error saving notes: {e}", ephemeral=True)
            except Exception:
                pass


class NotesEditButton(discord.ui.Button):
    def __init__(self, user_id: int, guild_id: int, current_notes: str):
        super().__init__(label="Edit Notes", style=discord.ButtonStyle.primary, emoji="📝")
        self.user_id = user_id
        self.guild_id = guild_id
        self.current_notes = current_notes

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "Only the person who ran this command can open the notes editor.", ephemeral=True
            )
            return
        try:
            modal = NotesEditModal(self.user_id, self.guild_id, self.current_notes)
            await interaction.response.send_modal(modal)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Couldn't open editor: {e}", ephemeral=True)
            except Exception:
                pass


class NotesEditView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int, current_notes: str):
        super().__init__(timeout=120)
        self.add_item(NotesEditButton(user_id, guild_id, current_notes))


class PersonalityEditModal(discord.ui.Modal):
    def __init__(self, current_personality: str, guild_id: int, set_by_user_id: int, conversation_mgr):
        super().__init__(title="Edit Server Personality")
        self.guild_id = guild_id
        self.set_by_user_id = set_by_user_id
        self.conversation_mgr = conversation_mgr

        prefill = current_personality if current_personality else _build_default_personality_text()
        self.personality_input = discord.ui.TextInput(
            label="Personality (submit blank to reset)",
            default=prefill[:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=False,
        )
        self.add_item(self.personality_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            personality_text = (self.personality_input.value or "").strip()
            if not personality_text:
                reset_guild_personality(self.guild_id)
                self.conversation_mgr.invalidate_personality_cache(self.guild_id)
                await interaction.response.send_message("✅ Personality reset to default.", ephemeral=True)
                return

            success = set_guild_personality(self.guild_id, personality_text, self.set_by_user_id)
            if success:
                self.conversation_mgr.invalidate_personality_cache(self.guild_id)
                await interaction.response.send_message("✅ Server personality updated.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "❌ Failed to update personality (database write failed).", ephemeral=True
                )
        except Exception as e:
            import traceback
            print(f"[PersonalityEditModal.on_submit] {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(f"❌ Error saving personality: {e}", ephemeral=True)
                else:
                    await interaction.response.send_message(f"❌ Error saving personality: {e}", ephemeral=True)
            except Exception:
                pass


class PersonalityEditButton(discord.ui.Button):
    def __init__(self, current_personality: str, guild_id: int, set_by_user_id: int, conversation_mgr):
        super().__init__(label="Edit Personality", style=discord.ButtonStyle.primary, emoji="✏️")
        self.current_personality = current_personality
        self.guild_id = guild_id
        self.set_by_user_id = set_by_user_id
        self.conversation_mgr = conversation_mgr

    async def callback(self, interaction: discord.Interaction):
        is_admin = (
            interaction.user.id == CREATOR_ID
            or (interaction.guild and interaction.guild.get_member(interaction.user.id)
                and interaction.guild.get_member(interaction.user.id).guild_permissions.administrator)
        )
        if not is_admin:
            try:
                await interaction.response.send_message(
                    "You need administrator permissions to edit the server personality.", ephemeral=True
                )
            except Exception:
                pass
            return

        try:
            modal = PersonalityEditModal(self.current_personality, self.guild_id, interaction.user.id, self.conversation_mgr)
            await interaction.response.send_modal(modal)
        except Exception as e:
            import traceback
            print(f"[PersonalityEditButton.callback] {type(e).__name__}: {e}")
            traceback.print_exc()
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ Couldn't open the editor: {e}", ephemeral=True)
            except Exception:
                pass


class PersonalityView(discord.ui.View):
    def __init__(self, current_personality: str, guild_id: int, set_by_user_id: int, conversation_mgr):
        super().__init__(timeout=900)
        self.add_item(PersonalityEditButton(current_personality, guild_id, set_by_user_id, conversation_mgr))
