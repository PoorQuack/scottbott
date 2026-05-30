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


def _needs_search(text: str) -> bool:
    """Return True if the message should trigger a web search."""
    if not text:
        return False
    stripped = text.strip()
    lowered = stripped.lower()
    if stripped.startswith('!'):
        return False
    no_search_patterns = [
        r'^!scott',
        r'^remember\s+',
        r'^forget\s+',
        r'^facts\s+',
        r'^imagine\s+',
        r'^analyze\s+this\s+file',
        r'^read\s+this\s+file',
    ]
    for pattern in no_search_patterns:
        if re.search(pattern, lowered):
            return False
    return True


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
