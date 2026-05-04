"""SQLite database module for persistent bot memory."""
import sqlite3
from datetime import datetime

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
    
    # Store conversation summaries per channel
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channel_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL UNIQUE,
            summary TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
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
    # Add user_name column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE memories ADD COLUMN user_name TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists

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
    # Add user_name column if it doesn't exist (for existing databases)
    try:
        cursor.execute('ALTER TABLE channel_messages ADD COLUMN user_name TEXT')
    except sqlite3.OperationalError:
        pass  # Column already exists
    
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_channel_messages 
        ON channel_messages(channel_id, message_order)
    ''')
    
    conn.commit()
    conn.close()
    print("Database initialized.")


def add_user_fact(user_id: int, fact: str, guild_id: int = None, category: str = "general", user_name: str = None):
    """Store a fact about a user. Preserves original created_at on update."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Try to update existing fact to preserve created_at
        cursor.execute('''
            UPDATE user_facts
            SET category = ?, updated_at = CURRENT_TIMESTAMP, guild_id = ?, user_name = ?
            WHERE user_id = ? AND fact = ?
        ''', (category, guild_id, user_name, user_id, fact))

        # If no row was updated, insert new fact
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


def get_user_facts(user_id: int, guild_id: int = None, limit: int = 10):
    """Retrieve facts about a user. Returns (fact, category, user_name)."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if guild_id:
        cursor.execute('''
            SELECT fact, category, user_name FROM user_facts 
            WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY updated_at DESC LIMIT ?
        ''', (user_id, guild_id, limit))
    else:
        cursor.execute('''
            SELECT fact, category, user_name FROM user_facts 
            WHERE user_id = ?
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


def get_recent_memories(guild_id: int = None, limit: int = 5, user_id: int = None):
    """Get recent important memories. Returns (content, user_id, user_name).

    When user_id is given, only memories created BY that user are returned.
    This prevents one user's '!scott remember me as X' from being injected
    into another user's system prompt — a leak that makes the model treat
    the first user's name as a global attribute.
    """
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    if user_id is not None and guild_id:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            WHERE user_id = ? AND (guild_id = ? OR guild_id IS NULL)
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, guild_id, limit))
    elif user_id is not None:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (user_id, limit))
    elif guild_id:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            WHERE guild_id = ?
            ORDER BY created_at DESC LIMIT ?
        ''', (guild_id, limit))
    else:
        cursor.execute('''
            SELECT content, user_id, user_name FROM memories
            ORDER BY created_at DESC LIMIT ?
        ''', (limit,))
    memories = cursor.fetchall()
    conn.close()
    return memories


def delete_memory(user_id: int, content: str) -> int:
    """Delete a specific memory. Returns number of rows deleted."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM memories WHERE user_id = ? AND content = ?', (user_id, content))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def build_memory_context(user_id: int = None, guild_id: int = None):
    """Build a context string with relevant memories for the AI.

    Only includes facts and memories belonging to the CURRENT user. This is
    important because the AI uses these to know who it's talking to — leaking
    another user's 'remember me as X' makes it call everyone X.
    """
    context_parts = []

    # Get user facts
    if user_id:
        facts = get_user_facts(user_id, guild_id)
        if facts:
            # Get the user name from the first fact (all should be same user)
            first_user_name = facts[0][2] if facts[0][2] else f"User {user_id}"
            context_parts.append(
                f"Facts about the CURRENT user ({first_user_name}, ID {user_id}). "
                f"These apply ONLY to this user, not to anyone else:"
            )
            for fact, category, user_name in facts:
                context_parts.append(f"  - [{category}] {fact}")

    # Get recent memories — ONLY for the current user.
    # Previously this fetched guild-wide memories, which leaked one user's
    # personal '!scott remember me as X' into other users' context.
    if user_id:
        memories = get_recent_memories(guild_id=guild_id, user_id=user_id)
        if memories:
            context_parts.append("\nThings the CURRENT user has asked you to remember:")
            for content, mem_user_id, mem_user_name in memories:
                context_parts.append(f"  - {content}")

    return "\n".join(context_parts) if context_parts else ""


def save_channel_messages(channel_id: int, messages: list):
    """Save conversation messages for a channel, replacing existing history.
    Accepts MessageWithMeta objects from conversation manager."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        # Clear existing messages for this channel
        cursor.execute('DELETE FROM channel_messages WHERE channel_id = ?', (channel_id,))
        
        # Insert new messages
        for idx, msg in enumerate(messages):
            # Handle MessageWithMeta wrapper or plain Content
            if hasattr(msg, 'content'):
                # It's a MessageWithMeta wrapper
                content_obj = msg.content
                user_id = getattr(msg, 'user_id', None)
                user_name = getattr(msg, 'user_name', None)
            else:
                # It's a plain Content object (legacy or model response)
                content_obj = msg
                user_id = None
                user_name = None
            
            role = getattr(content_obj, 'role', 'user')
            parts = getattr(content_obj, 'parts', [])
            
            # Extract text content and check for images
            text_parts = []
            has_image = False
            for part in parts:
                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
                elif hasattr(part, 'inline_data') and part.inline_data:
                    has_image = True
                    # Store image metadata, not the binary data
                    text_parts.append("[Image attached]")
            
            raw_content = "\n".join(text_parts) if text_parts else ""
            
            # Include user attribution in content for context
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
            role, content, has_image, user_id, db_user_name = row
            
            # Use stored user_name if available, otherwise parse from content
            user_name = db_user_name
            display_content = content
            
            if not user_name and role == 'user' and content.startswith('['):
                # Parse username from content (format: "[Username]: message")
                end_bracket = content.find(']: ')
                if end_bracket != -1:
                    user_name = content[1:end_bracket]
                    display_content = content[end_bracket + 3:]
            
            parts = [types.Part(text=display_content)]
            content_obj = types.Content(role=role, parts=parts)
            
            # Wrap in MessageWithMeta
            msg = MessageWithMeta(content_obj, user_id=user_id, user_name=user_name)
            messages.append(msg)
    except Exception as e:
        print(f"Error loading channel messages: {e}")
    finally:
        conn.close()
    return messages


def prune_channel_history(channel_id: int = None, max_messages: int = 50):
    """Prune old messages to keep only recent history. If channel_id is None, prunes all channels."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        if channel_id:
            # Keep only the most recent max_messages for this channel
            cursor.execute('''
                DELETE FROM channel_messages 
                WHERE channel_id = ? 
                AND message_order < (
                    SELECT MAX(message_order) - ? 
                    FROM channel_messages 
                    WHERE channel_id = ?
                )
            ''', (channel_id, max_messages, channel_id))
        else:
            # Prune all channels - keep last max_messages per channel
            cursor.execute('''
                DELETE FROM channel_messages 
                WHERE id IN (
                    SELECT id FROM channel_messages cm1
                    WHERE message_order < (
                        SELECT MAX(message_order) - ? 
                        FROM channel_messages cm2 
                        WHERE cm2.channel_id = cm1.channel_id
                    )
                )
            ''', (max_messages,))
        conn.commit()
    except Exception as e:
        print(f"Error pruning channel history: {e}")
    finally:
        conn.close()


def clear_channel_history(channel_id: int):
    """Clear all conversation history for a specific channel."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM channel_messages WHERE channel_id = ?', (channel_id,))
        conn.commit()
    except Exception as e:
        print(f"Error clearing channel history: {e}")
    finally:
        conn.close()


def get_active_channels(limit: int = 100):
    """Get list of channel IDs with conversation history, ordered by most recent activity."""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            SELECT channel_id, MAX(timestamp) as last_active
            FROM channel_messages
            GROUP BY channel_id
            ORDER BY last_active DESC
            LIMIT ?
        ''', (limit,))
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error getting active channels: {e}")
        return []
    finally:
        conn.close()
