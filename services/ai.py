import asyncio
import random
import time
from google import genai
from google.genai.errors import ServerError
from openai import AsyncOpenAI, APIError, BadRequestError
from config import (
    GOOGLE_API_KEY,
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_MODEL,
    NIM_MODEL_COMPLEX,
    NIM_TIMEOUT_SIMPLE,
    NIM_TIMEOUT_COMPLEX,
    NIM_REASONING_EFFORT,
    NIM_MAX_TOKENS,
    NIM_TOP_P,
    SCOTT_TEMP,
    GEMINI_MAX_RETRIES,
    GEMINI_BASE_DELAY,
    GEMINI_MAX_DELAY,
)

COMPLEX_TRIGGERS = [
    # Coding - more specific patterns
    "write a function", "debug this", "fix this code", "implement", "refactor",
    "traceback", "class definition", "def function", "import error",
    "syntax error", "runtime error", "help me code", "coding help",
    # Deep analysis - more specific patterns
    "explain in detail", "compare and contrast", "write an essay",
    "technical analysis", "system architecture", "code review",
    "algorithm design", "data structure", "database schema"
]

def needs_strong_model(message: str) -> bool:
    lowered = message.lower()
    return any(trigger in lowered for trigger in COMPLEX_TRIGGERS)

client = genai.Client(api_key=GOOGLE_API_KEY, http_options={'api_version': 'v1alpha'})

# NVIDIA NIM OpenAI-compatible client (timeout will be set dynamically)
nim_client = AsyncOpenAI(
    base_url=NIM_BASE_URL,
    api_key=NIM_API_KEY,
    timeout=NIM_TIMEOUT_SIMPLE,
)

# Runtime reasoning effort for the primary model.
# Default comes from .env (NIM_REASONING_EFFORT); toggled live by
# !scott light (low) and !scott heavy (high).
_VALID_EFFORTS = {"low", "medium", "high"}
_reasoning_mode = NIM_REASONING_EFFORT if NIM_REASONING_EFFORT in _VALID_EFFORTS else "low"


# Few-shot priming turns. Demonstrated user/assistant exchanges steer tone far
# harder than described rules — models (gpt-oss especially) imitate the pattern
# they see. These are injected right after the system prompt, before real history.
_STYLE_PRIMING = [
    {"role": "user", "content": "yo you up?"},
    {"role": "assistant", "content": "yeah what's good"},
    {"role": "user", "content": "explain how vpns work real quick"},
    {"role": "assistant", "content": "it tunnels your traffic through another server, so your ISP just sees encrypted junk and the site sees the server's IP instead of yours. decent for privacy, not magic though."},
    {"role": "user", "content": "i had the worst day lol"},
    {"role": "assistant", "content": "oof what happened"},
    {"role": "user", "content": "thanks man"},
    {"role": "assistant", "content": "anytime"},
]


def get_reasoning_mode() -> str:
    return _reasoning_mode


def set_reasoning_mode(mode: str) -> str:
    """Set the primary model's reasoning effort. Returns the applied mode."""
    global _reasoning_mode
    mode = (mode or "").strip().lower()
    if mode in _VALID_EFFORTS:
        _reasoning_mode = mode
    return _reasoning_mode


async def generate_content_with_retry(model, contents, config=None, max_retries=GEMINI_MAX_RETRIES):
    """Wrapper for client.aio.models.generate_content with exponential backoff retry logic."""
    last_exception = None
    for attempt in range(max_retries):
        try:
            return await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except ServerError as e:
            last_exception = e
            error_str = str(e)
            if "503" in error_str or "UNAVAILABLE" in error_str:
                if attempt < max_retries - 1:
                    delay = min(GEMINI_BASE_DELAY * (2 ** attempt), GEMINI_MAX_DELAY)
                    jitter = random.uniform(0, delay * 0.1)
                    total_delay = delay + jitter
                    print(f"Gemini 503 error (attempt {attempt + 1}/{max_retries}), retrying in {total_delay:.2f}s...")
                    await asyncio.sleep(total_delay)
                    continue
            raise
        except Exception:
            raise
    raise last_exception


def _extract_audio(response):
    """Extract audio bytes and text parts from Lyria/Gemini response."""
    audio_bytes = None
    text_parts = []
    seen_texts = set()

    def _walk_parts(parts):
        nonlocal audio_bytes
        for part in parts:
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data and not audio_bytes:
                audio_bytes = data
            txt = getattr(part, "text", None)
            if txt and txt not in seen_texts:
                text_parts.append(txt)
                seen_texts.add(txt)

    try:
        parts = getattr(response, "parts", None)
        if parts:
            _walk_parts(parts)
    except (AttributeError, TypeError) as walk_err:
        print(f"[song] response.parts walk failed: {walk_err}")

    if not audio_bytes:
        try:
            cands = getattr(response, "candidates", None)
            if cands:
                for cand in cands:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    parts = getattr(content, "parts", None) or []
                    _walk_parts(parts)
                    if audio_bytes:
                        break
        except (AttributeError, TypeError, IndexError) as walk_err:
            print(f"[song] response.candidates walk failed: {walk_err}")

    return audio_bytes, text_parts


async def generate_chat_with_nim(system_prompt: str, user_message: str, conversation_history: list = None) -> str:
    """Generate a chat response using NVIDIA NIM via OpenAI-compatible client with fallback models."""
    if not NIM_API_KEY:
        raise ValueError("NIM_API_KEY not set")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Prime tone with demonstrated example turns before the real conversation.
    messages.extend(_STYLE_PRIMING)

    if conversation_history:
        for msg in conversation_history:
            if not (hasattr(msg, 'role') and hasattr(msg, 'parts')):
                continue
            role = "assistant" if msg.role == "model" else "user"
            content = ""
            for part in (msg.parts or []):
                if hasattr(part, 'text') and part.text:
                    content += part.text
                elif hasattr(part, 'inline_data'):
                    content += "[image attachment]"
            if content:
                messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": user_message})

    # Single primary model (e.g. gpt-oss-120b). Reasoning effort is controlled
    # at runtime via !scott light / !scott heavy. "heavy" gets a longer timeout
    # since high reasoning takes longer. Fallback (below) is Gemini only.
    effort = get_reasoning_mode()
    timeout = NIM_TIMEOUT_COMPLEX if effort == "high" else NIM_TIMEOUT_SIMPLE
    models_to_try = [(NIM_MODEL, timeout)]

    for model, timeout in models_to_try:
        # Update client timeout dynamically
        nim_client.timeout = timeout

        # gpt-oss spends reasoning tokens out of max_tokens. On high effort the
        # reasoning alone can exceed a small budget, leaving no room for the
        # actual answer (empty reply). Give heavy mode a much larger ceiling.
        max_tokens = NIM_MAX_TOKENS
        if effort == "high":
            max_tokens = max(NIM_MAX_TOKENS, 8192)

        kwargs = {
            "model": model,
            "messages": messages,
            "temperature": SCOTT_TEMP,
            "top_p": NIM_TOP_P,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if effort:
            kwargs["reasoning_effort"] = effort

        try:
            start = time.time()
            completion = await nim_client.chat.completions.create(**kwargs)
            elapsed = time.time() - start
            print(f"[NIM] {model} (timeout={timeout}s) response time: {elapsed:.2f}s")
            if not completion.choices:
                print(f"[NIM] {model} Response has no choices. Full response: {completion}")
                continue
            return completion.choices[0].message.content
        except BadRequestError as e:
            body = str(e)
            print(f"[NIM] {model} BadRequest: {body}")
            if "reasoning_effort" in body and effort:
                print(f"[NIM] {model} Retrying without reasoning_effort...")
                kwargs.pop("reasoning_effort", None)
                try:
                    start = time.time()
                    completion = await nim_client.chat.completions.create(**kwargs)
                    elapsed = time.time() - start
                    print(f"[NIM] {model} (retry without reasoning_effort) response time: {elapsed:.2f}s")
                    return completion.choices[0].message.content
                except Exception as e2:
                    print(f"[NIM] {model} Retry failed: {type(e2).__name__}: {e2}")
            continue
        except APIError as e:
            print(f"[NIM] {model} API error: {type(e).__name__}: {e}")
            continue
        except Exception as e:
            print(f"[NIM] {model} Unexpected error: {type(e).__name__}: {e}")
            continue

    # Fallback to Gemini if all NIM models fail
    print("[NIM] All models failed, falling back to Gemini")
    try:
        contents = []
        if system_prompt:
            contents.append(genai.types.Part(text=system_prompt))
        if conversation_history:
            for msg in conversation_history:
                if not (hasattr(msg, 'role') and hasattr(msg, 'parts')):
                    continue
                role = "user" if msg.role == "user" else "model"
                for part in (msg.parts or []):
                    if hasattr(part, 'text') and part.text:
                        contents.append(genai.types.Part(text=part.text))
        contents.append(genai.types.Part(text=user_message))

        start = time.time()
        response = await generate_content_with_retry("gemini-2.5-flash", genai.types.Content(parts=contents))
        elapsed = time.time() - start
        print(f"[Gemini] Response time: {elapsed:.2f}s")

        text_parts = []
        for part in response.parts:
            if hasattr(part, 'text') and part.text:
                text_parts.append(part.text)
        return ''.join(text_parts)
    except Exception as e:
        print(f"[Gemini] Fallback failed: {type(e).__name__}: {e}")
        return None
