import re
import asyncio

# Lazy DDGS import
ddgs_available = False
try:
    from ddgs import DDGS
    ddgs_available = True
except ImportError:
    DDGS = None
    print("Warning: ddgs package not installed. Web search functionality will be disabled.")


GREETINGS = {
    "hi", "hello", "hey", "thanks", "thank you", "lol", "ok",
    "okay", "yes", "no", "sure", "cool", "nice", "haha", "bye",
}

SEARCH_SIGNALS = [
    "?", "what", "who", "when", "where", "how", "why",
    "latest", "news", "price", "weather", "score",
]


def _needs_search(text: str) -> bool:
    """Return True if the message should trigger a web search."""
    if not text:
        return False
    # Strip Discord mentions, emojis, collapse whitespace
    clean = re.sub(r"<@!?\d+>", "", text).strip().lower()
    if not clean or clean in GREETINGS:
        return False
    if len(clean.split()) <= 3 and "?" not in clean:
        return False
    if any(signal in clean for signal in SEARCH_SIGNALS):
        return True
    return False


async def web_search(query: str, num_results: int = 5) -> str:
    """Search via DuckDuckGo and return a formatted snippet string."""
    global ddgs_available, DDGS
    if DDGS is None:
        try:
            from ddgs import DDGS
            ddgs_available = True
        except ImportError:
            print("[web_search] ddgs package not available")
            return ""

    if not ddgs_available or DDGS is None:
        return ""

    try:
        results = await asyncio.to_thread(
            lambda: list(DDGS().text(query, max_results=num_results))
        )
        if not results:
            return ""
        lines = []
        for r in results:
            title = r.get("title", "").strip()
            body = r.get("body", "").replace("\n", " ").strip()
            href = r.get("href", "")
            lines.append(f"- {title}: {body} ({href})")
        summary = "\n".join(lines)
        print(f"[DEBUG] DDG search for {query!r} returned {len(results)} results")
        return summary
    except Exception as e:
        print(f"[WARN] Web search failed: {e}")
        return ""
