"""SQLite database module for persistent bot memory.

Core persistence layer for conversation history, user facts, notes,
and per-guild personality overrides.
"""
import sqlite3

DB_FILE = "scott_memory.db"


class MessageWithMeta:
    """Wrapper for Content that includes user metadata.
    Defined here to avoid circular imports with scottbott.py"""

    def __init__(self, content, user_id: int = None, user_name: str = None):
        self.content = content
        self.user_id = user_id
        self.user_name = user_name
        # Expose Content attributes for compatibility
        self.role = getattr(content, 'role', 'user')
        self.parts = getattr(content, 'parts', [])


def init_db():
    """Initialize the SQLite database with tables for memories."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    # Store facts about users (preferences, important info)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            guild_id INTEGER,
            fact TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, fact)
        )
    ''')
    # Add user_name column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE user_facts ADD COLUMN user_name TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

    # Store important events/milestones
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            user_name TEXT,
            guild_id INTEGER,
            content TEXT NOT NULL,
            importance INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    try:
        cursor.execute('ALTER TABLE memories ADD COLUMN user_name TEXT')
    except sqlite3.OperationalError:
        pass

    # Store conversation history per channel
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channel_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            message_order INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            has_image BOOLEAN DEFAULT 0,
            user_id INTEGER,
            user_name TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(channel_id, message_order)
        )
    ''')
    try:
        cursor.execute('ALTER TABLE channel_messages ADD COLUMN user_name TEXT')
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_channel_messages
        ON channel_messages(channel_id, message_order)
    ''')

    # Per-guild personality overrides (admin-configurable)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS guild_personality (
            guild_id INTEGER PRIMARY KEY,
            personality_text TEXT NOT NULL,
            set_by_user_id INTEGER,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Per-user freeform notes (editable via !scott editnotes / /editnotes)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_notes (
            user_id INTEGER NOT NULL,
            guild_id INTEGER,
            notes TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, guild_id)
        )
    ''')

    conn.commit()
    conn.close()
    print("Database initialized.")


def add_user_fact(user_id: int, fact: str, guild_id: int = None, category: str = "general", user_name: str = None):
    """Store a fact about a user. Preserves original created_at on update."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            UPDATE user_facts
            SET category = ?, updated_at = CURRENT_TIMESTAMP, guild_id = ?, user_name = ?
            WHERE user_id = ? AND fact = ?
        ''', (category, guild_id, user_name, user_id, fact))

        if cursor.rowcount == 0:
            cursor.execute('''
                INSERT INTO user_facts (user_id, user_name, guild_id, fact, category, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ''', (user_id, user_name, guild_id, fact, category))

        conn.commit()
    except Exception as e:
        print(f"Error saving user fact: {e}")
    finally:
        conn.close()


def get_user_facts(user_id: int, guild_id: int = None, limit: int = 10, include_all_guilds: bool = False):
    """Retrieve facts about a user. Returns (fact, category, user_name).

    Args:
        user_id: The user to retrieve facts for
        guild_id: Guild context (None for global/DM context)
        limit: Maximum facts to return
        include_all_guilds: If True and guild_id is None, return facts from all guilds.
                           If False (default), guild_id=None only returns global facts.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if guild_id:
        cursor.execute('''
            SELECT fact, category, user_name FROM user_facts
            WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY updated_at DESC LIMIT ?
        ''', (user_id, guild_id, limit))
    elif include_all_guilds:
        cursor.execute('''
            SELECT fact, category, user_name FROM user_facts
            WHERE user_id = ?
            ORDER BY updated_at DESC LIMIT ?
        ''', (user_id, limit))
    else:
        # DMs / global: only return global facts to prevent cross-guild leaks
        cursor.execute('''
            SELECT fact, category, user_name FROM user_facts
            WHERE user_id = ? AND guild_id IS NULL
            ORDER BY updated_at DESC LIMIT ?
        ''', (user_id, limit))
    facts = cursor.fetchall()
    conn.close()
    return facts


def add_memory(content: str, user_id: int = None, guild_id: int = None, importance: int = 1, user_name: str = None):
    """Store a general memory/event."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO memories (user_id, user_name, guild_id, content, importance)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, user_name, guild_id, content, importance))
        conn.commit()
    except Exception as e:
        print(f"Error saving memory: {e}")
    finally:
        conn.close()


def _get_recent_memories(user_id: int, guild_id: int = None, limit: int = 5):
    """Internal: get recent memories for the given user. Returns (content, user_id, user_name).

    ONLY memories created BY the specified user_id are returned. This prevents
    one user's '!scott remember me as X' from being injected into another
    user's prompt. user_id is required — there is no "all users" mode.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if guild_id:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, guild_id, limit))
    else:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit))
    memories = cursor.fetchall()
    conn.close()
    return memories


def search_memories(user_id: int, keyword: str, guild_id: int = None) -> list:
    """Search both memories and user_facts for a user by keyword (case-insensitive partial match).
    When guild_id is given, restricts results to that guild plus global (NULL) entries.
    Returns list of (table, id, text) tuples."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    results = []
    kw = f"%{keyword.lower()}%"

    if guild_id:
        cursor.execute(
            'SELECT id, content FROM memories WHERE user_id = ? AND LOWER(content) LIKE ? '
            'AND (guild_id = ? OR guild_id IS NULL)',
            (user_id, kw, guild_id)
        )
    else:
        cursor.execute(
            'SELECT id, content FROM memories WHERE user_id = ? AND LOWER(content) LIKE ? '
            'AND guild_id IS NULL',
            (user_id, kw)
        )
    for row in cursor.fetchall():
        results.append(('memory', row[0], row[1]))

    if guild_id:
        cursor.execute(
            'SELECT id, fact FROM user_facts WHERE user_id = ? AND LOWER(fact) LIKE ? '
            'AND (guild_id = ? OR guild_id IS NULL)',
            (user_id, kw, guild_id)
        )
    else:
        cursor.execute(
            'SELECT id, fact FROM user_facts WHERE user_id = ? AND LOWER(fact) LIKE ? '
            'AND guild_id IS NULL',
            (user_id, kw)
        )
    for row in cursor.fetchall():
        results.append(('fact', row[0], row[1]))

    conn.close()
    return results


def delete_by_id(table: str, row_id: int) -> int:
    """Delete a memory or fact by its database ID. Returns rows deleted."""
    if table not in ('memory', 'fact'):
        return 0
    real_table = 'memories' if table == 'memory' else 'user_facts'
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(f'DELETE FROM {real_table} WHERE id = ?', (row_id,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def get_user_notes(user_id: int, guild_id: int = None) -> str:
    """Return the user's freeform notes string, or empty string if none."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        if guild_id:
            cursor.execute(
                'SELECT notes FROM user_notes WHERE user_id = ? AND guild_id = ?',
                (user_id, guild_id)
            )
        else:
            cursor.execute(
                'SELECT notes FROM user_notes WHERE user_id = ? AND guild_id IS NULL',
                (user_id,)
            )
        row = cursor.fetchone()
        return row[0] if row else ""
    except Exception as e:
        print(f"[UserNotes] Error getting notes for user {user_id}: {e}")
        return ""
    finally:
        conn.close()


def set_user_notes(user_id: int, notes: str, guild_id: int = None) -> bool:
    """Save (upsert) freeform notes for a user. Returns True on success."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO user_notes (user_id, guild_id, notes, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, guild_id) DO UPDATE SET
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, guild_id, notes))
        conn.commit()
        return True
    except Exception as e:
        print(f"[UserNotes] Error setting notes for user {user_id}: {e}")
        return False
    finally:
        conn.close()


def clear_user_notes(user_id: int, guild_id: int = None) -> bool:
    """Delete a user's notes. Returns True if a row was deleted."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        if guild_id:
            cursor.execute(
                'DELETE FROM user_notes WHERE user_id = ? AND guild_id = ?',
                (user_id, guild_id)
            )
        else:
            cursor.execute(
                'DELETE FROM user_notes WHERE user_id = ? AND guild_id IS NULL',
                (user_id,)
            )
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"[UserNotes] Error clearing notes for user {user_id}: {e}")
        return False
    finally:
        conn.close()


def build_memory_context(user_id: int = None, guild_id: int = None):
    """Build a context string with relevant memories for the AI.

    Only includes facts and memories belonging to the CURRENT user. This is
    important because the AI uses these to know who it's talking to; leaking
    another user's 'remember me as X' makes it call everyone X.
    """
    context_parts = []

    if user_id:
        facts = get_user_facts(user_id, guild_id)
        if facts:
            first_user_name = facts[0][2] if facts[0][2] else f"User {user_id}"
            context_parts.append(
                f"Facts about the CURRENT user ({first_user_name}, ID {user_id}). "
                f"These apply ONLY to this user, not to anyone else:"
            )
            for fact, category, _user_name in facts:
                context_parts.append(f"  - [{category}] {fact}")

    if user_id:
        memories = _get_recent_memories(guild_id=guild_id, user_id=user_id)
        if memories:
            context_parts.append("\nThings the CURRENT user has asked you to remember:")
            for content, _mem_user_id, _mem_user_name in memories:
                context_parts.append(f"  - {content}")

    if user_id:
        notes = get_user_notes(user_id, guild_id)
        if notes:
            context_parts.append("\nUser's personal notes (they wrote these themselves for you to know):")
            context_parts.append(notes)

    return "\n".join(context_parts) if context_parts else ""


def save_channel_messages(channel_id: int, messages: list):
    """Save conversation messages for a channel, replacing existing history.
    Accepts MessageWithMeta objects from conversation manager."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM channel_messages WHERE channel_id = ?', (channel_id,))

        for idx, msg in enumerate(messages):
            # Handle MessageWithMeta wrapper or plain Content
            if hasattr(msg, 'content'):
                content_obj = msg.content
                user_id = getattr(msg, 'user_id', None)
                user_name = getattr(msg, 'user_name', None)
            else:
                content_obj = msg
                user_id = None
                user_name = None

            role = getattr(content_obj, 'role', 'user')
            parts = getattr(content_obj, 'parts', [])

            text_parts = []
            has_image = False
            for part in parts:
                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
                elif hasattr(part, 'inline_data') and part.inline_data:
                    has_image = True
                    text_parts.append("[Image attached]")

            raw_content = "\n".join(text_parts) if text_parts else ""

            # Prefix user attribution into stored content for context
            if role == 'user' and user_name:
                content = f"[{user_name}]: {raw_content}"
            else:
                content = raw_content

            cursor.execute('''
                INSERT INTO channel_messages
                (channel_id, message_order, role, content, has_image, user_id, user_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (channel_id, idx, role, content, has_image, user_id, user_name))

        conn.commit()
    except Exception as e:
        print(f"Error saving channel messages: {e}")
    finally:
        conn.close()


def load_channel_messages(channel_id: int, limit: int = 50):
    """Load conversation history for a channel as MessageWithMeta objects."""
    from google.genai import types
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    messages = []
    try:
        cursor.execute('''
            SELECT role, content, has_image, user_id, user_name FROM channel_messages
            WHERE channel_id = ?
            ORDER BY message_order
            LIMIT ?
        ''', (channel_id, limit))

        for row in cursor.fetchall():
            role, content, _has_image, user_id, db_user_name = row

            user_name = db_user_name
            if not user_name and role == 'user' and content.startswith('['):
                end_bracket = content.find(']: ')
                if end_bracket != -1:
                    user_name = content[1:end_bracket]
            # Preserve full content including the [Username]: prefix
            # so downstream models can identify who said what.

            parts = [types.Part(text=content)]
            content_obj = types.Content(role=role, parts=parts)
            messages.append(MessageWithMeta(content_obj, user_id=user_id, user_name=user_name))
    except Exception as e:
        print(f"Error loading channel messages: {e}")
    finally:
        conn.close()
    return messages


def set_guild_personality(guild_id: int, personality_text: str, set_by_user_id: int) -> bool:
    """Set or update the per-guild personality override.
    Returns True if successful."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO guild_personality (guild_id, personality_text, set_by_user_id, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(guild_id) DO UPDATE SET
                personality_text = excluded.personality_text,
                set_by_user_id = excluded.set_by_user_id,
                updated_at = CURRENT_TIMESTAMP
        ''', (guild_id, personality_text, set_by_user_id))
        conn.commit()
        print(f"[GuildPersonality] Set personality for guild {guild_id} by user {set_by_user_id}")
        return True
    except Exception as e:
        print(f"[GuildPersonality] Error setting personality for guild {guild_id}: {e}")
        return False
    finally:
        conn.close()


def get_guild_personality(guild_id: int) -> str:
    """Get the per-guild personality override text. Returns None if not set."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT personality_text FROM guild_personality WHERE guild_id = ?',
            (guild_id,)
        )
        result = cursor.fetchone()
        return result[0] if result else None
    except Exception as e:
        print(f"[GuildPersonality] Error getting personality for guild {guild_id}: {e}")
        return None
    finally:
        conn.close()


def reset_guild_personality(guild_id: int) -> bool:
    """Delete the per-guild personality override. Returns True if deleted."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM guild_personality WHERE guild_id = ?', (guild_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            print(f"[GuildPersonality] Reset personality for guild {guild_id}")
        return deleted
    except Exception as e:
        print(f"[GuildPersonality] Error resetting personality for guild {guild_id}: {e}")
        return False
    finally:
        conn.close()
