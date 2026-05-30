import asyncio
import random
import httpx
from google import genai
from google.genai.errors import ServerError
from config import (
    GOOGLE_API_KEY,
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_MODEL,
    NIM_TIMEOUT,
    NIM_REASONING_EFFORT,
    NIM_MAX_TOKENS,
    NIM_TOP_P,
    SCOTT_TEMP,
    GEMINI_MAX_RETRIES,
    GEMINI_BASE_DELAY,
    GEMINI_MAX_DELAY,
)

client = genai.Client(api_key=GOOGLE_API_KEY, http_options={'api_version': 'v1alpha'})


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
    """Generate a chat response using NVIDIA NIM (OpenAI-compatible)."""
    if not NIM_API_KEY:
        raise ValueError("NIM_API_KEY not set")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

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

    headers = {
        "Authorization": f"Bearer {NIM_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NIM_MODEL,
        "messages": messages,
        "temperature": SCOTT_TEMP,
        "top_p": NIM_TOP_P,
        "max_tokens": NIM_MAX_TOKENS,
        "stream": False,
    }
    if NIM_REASONING_EFFORT:
        payload["reasoning_effort"] = NIM_REASONING_EFFORT

    endpoint = NIM_BASE_URL.rstrip("/") + "/chat/completions"

    try:
        async with httpx.AsyncClient() as http:
            resp = await http.post(endpoint, headers=headers, json=payload, timeout=NIM_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:500]
        except Exception:
            pass
        print(f"[NIM] HTTP error: {e.response.status_code} - {body}")
        if e.response.status_code == 400 and "reasoning_effort" in body and NIM_REASONING_EFFORT:
            print("[NIM] Retrying without reasoning_effort...")
            payload.pop("reasoning_effort", None)
            try:
                async with httpx.AsyncClient() as http:
                    resp = await http.post(endpoint, headers=headers, json=payload, timeout=NIM_TIMEOUT)
                    resp.raise_for_status()
                    return resp.json()["choices"][0]["message"]["content"]
            except Exception as e2:
                print(f"[NIM] Retry failed: {type(e2).__name__}: {e2}")
        return None
    except httpx.RequestError as e:
        print(f"[NIM] Request error: {e}")
        return None
    except (KeyError, IndexError) as e:
        print(f"[NIM] Response parsing error: {e}")
        return None
    except Exception as e:
        print(f"[NIM] Unexpected error: {type(e).__name__}: {e}")
        return None
