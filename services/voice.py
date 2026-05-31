"""Voice services: Whisper Large V3 (STT) and Magpie TTS, both via NVIDIA Riva gRPC.

Both models live on the NVCF gRPC gateway (grpc.nvcf.nvidia.com:443) and
authenticate with the same NIM_API_KEY plus a per-model function-id.

Public API:
    transcribe(pcm16, sample_rate)  -> str        (Whisper)
    synthesize(text)               -> bytes (WAV)  (Magpie)

Both calls are blocking gRPC, so callers should run them via
asyncio.to_thread(...) to avoid blocking the Discord event loop.
"""
import io
import wave

try:
    import riva.client
    _RIVA_AVAILABLE = True
except ImportError:  # nvidia-riva-client not installed yet
    riva = None
    _RIVA_AVAILABLE = False

from config import (
    NIM_API_KEY,
    RIVA_GRPC_URI,
    ASR_FUNCTION_ID,
    ASR_MODEL,
    MAGPIE_FUNCTION_ID,
    MAGPIE_VOICE,
    VOICE_LANGUAGE_CODE,
)

# Magpie returns 44.1 kHz mono PCM; we wrap it as WAV for FFmpeg playback.
_TTS_SAMPLE_RATE = 44100


def _make_auth(function_id: str):
    """Build a Riva Auth object pointed at the NVCF gateway for one function."""
    if not _RIVA_AVAILABLE:
        raise RuntimeError("nvidia-riva-client not installed. `pip install nvidia-riva-client`")
    if not NIM_API_KEY:
        raise RuntimeError("NIM_API_KEY not set")
    return riva.client.Auth(
        uri=RIVA_GRPC_URI,
        use_ssl=True,
        metadata_args=[
            ["function-id", function_id],
            ["authorization", f"Bearer {NIM_API_KEY}"],
        ],
    )


# Lazily-built service singletons (so import never fails before deps installed).
_asr_service = None
_tts_service = None


def _get_asr():
    global _asr_service
    if _asr_service is None:
        print(f"[VOICE] ASR model: {ASR_MODEL} (function-id {ASR_FUNCTION_ID})")
        _asr_service = riva.client.ASRService(_make_auth(ASR_FUNCTION_ID))
    return _asr_service


def _get_tts():
    global _tts_service
    if _tts_service is None:
        _tts_service = riva.client.SpeechSynthesisService(_make_auth(MAGPIE_FUNCTION_ID))
    return _tts_service


# Common Whisper hallucination patterns to filter
_BAD_PHRASES = ["amara.org", "subtitles by", "transcribed by", "www.", ".com", "http://", "https://"]

def transcribe(pcm16: bytes, sample_rate: int = 16000) -> str:
    """Transcribe raw mono 16-bit PCM with Whisper Large V3. Returns text (may be '')."""
    if not pcm16:
        return ""
    try:
        config = riva.client.RecognitionConfig(
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hertz=sample_rate,
            language_code=VOICE_LANGUAGE_CODE,
            max_alternatives=1,
            enable_automatic_punctuation=True,
        )
        response = _get_asr().offline_recognize(pcm16, config)
        for result in response.results:
            if result.alternatives:
                text = result.alternatives[0].transcript.strip()
                if text:
                    # Filter common hallucination patterns
                    text_lower = text.lower()
                    if any(phrase in text_lower for phrase in _BAD_PHRASES):
                        print(f"[VOICE] Filtered hallucination: {text}")
                        return ""
                    return text
        return ""
    except Exception as e:
        print(f"[VOICE] Whisper STT failed: {type(e).__name__}: {e}")
        return ""


def synthesize(text: str) -> bytes:
    """Synthesize speech with Magpie TTS. Returns WAV bytes, or None on failure."""
    if not text or not text.strip():
        return None
    try:
        resp = _get_tts().synthesize(
            text=text,
            voice_name=MAGPIE_VOICE,
            language_code=VOICE_LANGUAGE_CODE,
            encoding=riva.client.AudioEncoding.LINEAR_PCM,
            sample_rate_hz=_TTS_SAMPLE_RATE,
        )
        pcm = resp.audio  # raw LINEAR_PCM bytes
        if not pcm:
            return None
        # Wrap PCM as a WAV container so discord.FFmpegPCMAudio can play it.
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(_TTS_SAMPLE_RATE)
            wf.writeframes(pcm)
        buf.seek(0)
        return buf.getvalue()
    except Exception as e:
        print(f"[VOICE] Magpie TTS failed: {type(e).__name__}: {e}")
        return None


def voice_ready() -> tuple:
    """Return (ok, reason) describing whether voice deps/keys are ready."""
    if not _RIVA_AVAILABLE:
        return False, "nvidia-riva-client not installed"
    if not NIM_API_KEY:
        return False, "NIM_API_KEY not set"
    return True, "ok"
