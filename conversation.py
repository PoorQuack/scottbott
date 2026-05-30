import time
import re
import hashlib
import asyncio
from collections import defaultdict, deque
from config import CONTEXT_LIMIT
from memory import (
    save_channel_messages,
    load_channel_messages,
    MessageWithMeta,
    add_user_fact,
)

recent_facts_tracker = defaultdict(lambda: deque(maxlen=10))
FACT_EXTRACTION_COOLDOWN = 60


class PersistentConversationManager:
    """
    Hybrid conversation storage: SQLite for persistence, dict for hot cache.
    - Survives bot restarts (loads from DB on first access)
    - Memory-efficient (LRU eviction, saves to DB before eviction)
    - Async auto-save for durability without blocking
    """

    def __init__(self, cache_size: int = 20, context_limit: int = 50, auto_save_interval: int = 60):
        self._cache = {}
        self._cache_size = cache_size
        self._context_limit = context_limit
        self._access_order = []
        self._lock = asyncio.Lock()
        self._auto_save_interval = auto_save_interval
        self._pending_saves = set()
        self._initialized = False

    async def _auto_save_loop(self):
        while True:
            await asyncio.sleep(self._auto_save_interval)
            await self._flush_pending_saves()

    async def _flush_pending_saves(self):
        async with self._lock:
            pending = list(self._pending_saves)
            self._pending_saves.clear()

        for channel_id in pending:
            if channel_id in self._cache:
                save_channel_messages(channel_id, self._cache[channel_id])
                print(f"Auto-saved conversation history for channel {channel_id}")

    def _mark_dirty(self, channel_id: int):
        self._pending_saves.add(channel_id)

    async def _evict_oldest(self):
        if not self._access_order:
            return

        oldest = self._access_order.pop(0)
        if oldest in self._cache:
            save_channel_messages(oldest, self._cache[oldest])
            print(f"Evicted and saved channel {oldest} from cache")
            del self._cache[oldest]
        self._pending_saves.discard(oldest)

    async def get_context(self, channel_id: int) -> list:
        async with self._lock:
            if channel_id in self._cache:
                if channel_id in self._access_order:
                    self._access_order.remove(channel_id)
                self._access_order.append(channel_id)
                return [m.content for m in self._cache[channel_id]]

            messages = load_channel_messages(channel_id, self._context_limit)

            if len(self._cache) >= self._cache_size:
                await self._evict_oldest()

            self._cache[channel_id] = messages
            self._access_order.append(channel_id)
            print(f"Loaded conversation history for channel {channel_id} ({len(messages)} messages)")
            return [m.content for m in messages]

    async def append_message(self, channel_id: int, content, user_id: int = None, user_name: str = None):
        async with self._lock:
            if channel_id not in self._cache:
                messages = load_channel_messages(channel_id, self._context_limit)
                if len(self._cache) >= self._cache_size:
                    await self._evict_oldest()
                self._cache[channel_id] = messages
                self._access_order.append(channel_id)

            message = MessageWithMeta(content, user_id=user_id, user_name=user_name)
            self._cache[channel_id].append(message)

            if len(self._cache[channel_id]) > self._context_limit:
                self._cache[channel_id] = self._cache[channel_id][-self._context_limit:]

            self._mark_dirty(channel_id)

    async def shutdown(self):
        print("Shutting down conversation manager, saving all pending channels...")
        await self._flush_pending_saves()
        async with self._lock:
            for channel_id, messages in self._cache.items():
                save_channel_messages(channel_id, messages)
        print("All conversation history saved.")

    def invalidate_personality_cache(self, guild_id: int):
        pass


async def _maybe_extract_fact(message) -> bool:
    user_id = message.author.id
    content = message.content.strip()
    content_lower = content.lower()

    if len(content) < 10 or len(content) > 500:
        return False

    quality_patterns = [
        r'\bmy\s+(name|age|birthday|birthdate|job|work|location|city|country)\s+(is|was)\b',
        r'\bi\s+(am|was)\s+(a|an)\s+\w+\s+(student|developer|engineer|designer|artist|musician|writer|teacher|doctor|nurse|lawyer|chef)\b',
        r'\bi\s+(like|love|enjoy|hate|prefer)\s+(to\s+)?\w+\b',
        r'\bi\s+(speak|know|learn|study)\s+\w+\s*(language|spanish|french|german|japanese|chinese|korean|italian|portuguese|russian|arabic|hindi)?\b',
        r'\bmy\s+favorite\s+(color|food|movie|show|game|band|artist|song|book|animal|season|hobby)\s+(is|are)\b',
        r'\bi\s+(have|own|got)\s+(a|an)\s+(pet|dog|cat|bird|car|bike|computer|laptop|phone)\b',
        r'\bi\s+(live| grew\s+up|was\s+born)\s+(in|at)\b',
    ]

    matched_pattern = False
    for pattern in quality_patterns:
        if re.search(pattern, content_lower):
            matched_pattern = True
            break

    if not matched_pattern:
        return False

    now = time.time()
    user_history = recent_facts_tracker[user_id]

    while user_history and now - user_history[0][0] > FACT_EXTRACTION_COOLDOWN:
        user_history.popleft()

    if user_history and now - user_history[-1][0] < FACT_EXTRACTION_COOLDOWN:
        return False

    normalized = re.sub(r'[^\w\s]', '', content_lower).strip()
    normalized = re.sub(r'\s+', ' ', normalized)
    content_hash = hashlib.sha256(normalized.encode()).hexdigest()[:16]

    for ts, seen_hash in user_history:
        if seen_hash == content_hash:
            return False

    fact_text = content
    name_match = re.search(r'\bmy\s+name\s+is\s+([\w\s]+?)(?:\.|,|;|$|\band\b)', content_lower)
    if name_match:
        fact_text = f"My name is {name_match.group(1).strip().title()}"

    preference_match = re.search(r'\b(i\s+(?:like|love|enjoy|hate|prefer))\s+(.+?)(?:\.|,|;|$|\band\b|\bbut\b)', content_lower)
    if preference_match:
        fact_text = f"{preference_match.group(1).capitalize()} {preference_match.group(2).strip()}"

    add_user_fact(
        user_id=user_id,
        user_name=message.author.display_name,
        fact=fact_text,
        guild_id=message.guild.id if message.guild else None,
        category="auto_extracted"
    )

    user_history.append((now, content_hash))
    return True
