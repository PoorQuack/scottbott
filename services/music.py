import io
import re as _re
import traceback
from google.genai import types
from services.ai import generate_content_with_retry, _extract_audio
from config import SONG_MODEL_CLIP, SONG_MODEL_PRO


async def generate_song(prompt: str, long_version: bool = False):
    """Generate a song using Lyria via Gemini. Returns (audio_bytes, text_parts, file_ext, label) or raises."""
    if long_version:
        song_model = SONG_MODEL_PRO
        label = "full song"
        file_ext = "wav"
    else:
        song_model = SONG_MODEL_CLIP
        label = "30-sec clip"
        file_ext = "mp3"

    if not prompt:
        raise ValueError("Prompt is required")

    gen_config = None
    try:
        gen_config = types.GenerateContentConfig(
            response_modalities=["AUDIO", "TEXT"],
        )
    except (ValueError, TypeError) as config_err:
        print(f"[song] Model {song_model} doesn't support response_modalities, falling back to no config: {config_err}")
        gen_config = None

    response = await generate_content_with_retry(
        model=song_model,
        contents=prompt,
        config=gen_config,
    )

    audio_bytes, text_parts = _extract_audio(response)

    if not audio_bytes:
        model_text = "".join(text_parts).strip() if text_parts else ""
        print(f"[song] No audio found. Response type: {type(response).__name__}")
        print(f"[song] Response attrs: {[a for a in dir(response) if not a.startswith('_')][:20]}")
        if model_text:
            print(f"[song] Model text response: {model_text[:1000]}")
        try:
            pf = getattr(response, "prompt_feedback", None)
            if pf:
                print(f"[song] prompt_feedback: {pf}")
            cands = getattr(response, "candidates", None) or []
            for i, c in enumerate(cands):
                fr = getattr(c, "finish_reason", None)
                sr = getattr(c, "safety_ratings", None)
                print(f"[song] candidate[{i}] finish_reason={fr} safety_ratings={sr}")
        except Exception:
            pass
        raise RuntimeError(f"no_audio:{model_text}")

    stub = _re.sub(r"[^a-zA-Z0-9_-]+", "_", prompt[:40]).strip("_") or "song"
    filename = f"scottbott_{stub}.{file_ext}"
    return audio_bytes, text_parts, file_ext, label, filename


def format_song_error(e: Exception) -> str:
    """Turn a song-generation exception into a user-facing message."""
    err_text = str(e)
    if err_text.startswith("no_audio:"):
        model_text = err_text[len("no_audio:"):].strip()
        if model_text:
            return f"❌ The model didn't generate audio. It said:\n> {model_text[:500]}"
        return ("❌ The model returned no audio and no text. "
                "Most likely Lyria 3 isn't enabled on your project, "
                "or your prompt triggered a content filter. "
                "Check the bot console for `[song]` diagnostic output.")
    if "PERMISSION_DENIED" in err_text or "403" in err_text:
        return ("❌ Lyria 3 isn't enabled on your project, or your API key lacks access. "
                "Enable music generation at https://aistudio.google.com/")
    if "NOT_FOUND" in err_text or ("model" in err_text.lower() and "not found" in err_text.lower()):
        return ("❌ Model not available. Lyria 3 may need allowlist access "
                "on your account. Check https://ai.google.dev/gemini-api/docs/music-generation")
    if "RESOURCE_EXHAUSTED" in err_text or "429" in err_text:
        return "❌ Rate limited or quota exhausted on music generation."
    return f"❌ Song Error: {type(e).__name__}: {err_text[:300]}"
