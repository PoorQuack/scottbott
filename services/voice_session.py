"""Voice conversation loop for Scottbott.

Flow per user utterance:
    capture PCM (discord-ext-voice-recv, 48kHz stereo)
      -> buffer until silence (VAD)
      -> downmix/resample to 16kHz mono
      -> Whisper transcribe
      -> gpt-oss generate reply
      -> Magpie synthesize
      -> play back in the voice channel

discord-ext-voice-recv delivers audio on a separate thread, so the sink only
buffers bytes; an async VAD loop on the bot's event loop does the heavy work.
"""
import time
import asyncio
import threading
import tempfile
import os
import audioop
import wave

import discord

try:
    from discord.ext import voice_recv
    _RECV_AVAILABLE = True
except ImportError:
    voice_recv = None
    _RECV_AVAILABLE = False

try:
    import numpy as np
    _NUMPY = True
except ImportError:
    np = None
    _NUMPY = False

from config import VOICE_SILENCE_SECONDS
from services.voice import transcribe, synthesize
from services.ai import generate_chat_with_nim
from prompts import get_system_prompt

# discord-ext-voice-recv delivers 48kHz, 16-bit, stereo PCM.
_IN_RATE = 48000
_TARGET_RATE = 16000

# All debug WAVs go to <project>/voice_debug/ so they're always easy to find,
# regardless of what directory the bot was launched from.
_DEBUG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voice_debug")


def _debug_path(name: str) -> str:
    os.makedirs(_DEBUG_DIR, exist_ok=True)
    return os.path.join(_DEBUG_DIR, name)


def _highpass(pcm16_mono: bytes, fc: float = 90.0, rate: int = _TARGET_RATE) -> bytes:
    """Biquad high-pass (RBJ cookbook) to strip sub-90Hz rumble that masks speech.

    The captured PCM carries a large infrasonic component (~14-42Hz) that swamps
    the voice and makes Whisper hear muffled noise. Removing it leaves a normal
    speech spectrum. Stateless per utterance (each flush is filtered fresh).
    """
    if not _NUMPY or not pcm16_mono:
        return pcm16_mono
    import math
    x = np.frombuffer(pcm16_mono, dtype=np.int16).astype(np.float64)
    Q = 0.707
    wn = 2 * math.pi * fc / rate
    al = math.sin(wn) / (2 * Q)
    cw = math.cos(wn)
    a0 = 1 + al
    b0 = (1 + cw) / 2 / a0
    b1 = -(1 + cw) / a0
    b2 = (1 + cw) / 2 / a0
    a1 = -2 * cw / a0
    a2 = (1 - al) / a0
    # Apply the biquad twice (4th-order, ~24 dB/oct) for strong rumble rejection.
    for _ in range(2):
        y = np.empty_like(x)
        x1 = x2 = y1 = y2 = 0.0
        for i in range(len(x)):
            xi = x[i]
            yi = b0 * xi + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            y[i] = yi
            x2, x1 = x1, xi
            y2, y1 = y1, yi
        x = y
    return np.clip(x, -32768, 32767).astype(np.int16).tobytes()


# Set to True to dump what Riva receives (writes debug_*.wav in the cwd).
_DEBUG_DUMP = False

# Below this RMS the utterance is treated as silence (Whisper hallucinates
# "Subtitles by the Amara.org community" etc. on near-silent input).
_SILENCE_RMS = 120
# Target peak after normalization (~ -3 dBFS) so quiet mic input still
# reaches Whisper at a healthy level without re-introducing clipping.
_TARGET_PEAK = 23000


def _to_mono16k(raw48k_stereo: bytes) -> bytes:
    """Convert a full raw 48kHz stereo utterance to clean 16kHz mono for Whisper.

    One single pass over the whole utterance: downmix -> resample -> high-pass ->
    silence gate -> normalize. Returns b'' when the audio is just noise/silence so
    the caller skips Whisper instead of feeding it hallucination-bait.
    """
    if not raw48k_stereo:
        return b''
    # Downmix stereo -> mono, then resample 48k -> 16k in one shot (fresh state).
    mono48k = audioop.tomono(raw48k_stereo, 2, 0.5, 0.5)
    pcm16k, _ = audioop.ratecv(mono48k, 2, 1, _IN_RATE, _TARGET_RATE, None)
    out = _highpass(pcm16k)

    if _NUMPY and out:
        x = np.frombuffer(out, dtype=np.int16).astype(np.float64)
        rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
        peak = float(np.max(np.abs(x))) if len(x) else 0.0
        clip_pct = float(np.mean(np.abs(x) >= 32000) * 100) if len(x) else 0.0
        print(f"[AUDIO] rms={rms:.0f} peak={peak:.0f} clipping={clip_pct:.2f}%")
        if clip_pct >= 0.5:
            print("[AUDIO] *** INPUT IS CLIPPING — your mic is too loud. "
                  "Lower Discord Input Volume / Windows mic level. This distorts "
                  "speech and makes Whisper hallucinate. ***")
        if rms < _SILENCE_RMS or peak < 1:
            print("[AUDIO] below silence threshold, skipping")
            return b''
        gain = _TARGET_PEAK / peak
        gain = max(1.0, min(gain, 12.0))  # only amplify, cap to avoid blowing up noise
        x = np.clip(x * gain, -32768, 32767).astype(np.int16)
        out = x.tobytes()

    if _DEBUG_DUMP:
        path = _debug_path(f"debug_{int(time.time())}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(_TARGET_RATE)
            wf.writeframes(out)
        print(f"[DEBUG] saved processed audio -> {path}")

    return out


class _ScottSink(voice_recv.AudioSink if _RECV_AVAILABLE else object):
    """Buffers incoming PCM per user; thread-safe for the async VAD loop."""

    def __init__(self, session):
        if _RECV_AVAILABLE:
            super().__init__()
        self.session = session

    def wants_opus(self) -> bool:
        return False  # we want decoded PCM

    def write(self, user, data):
        try:
            pcm = getattr(data, "pcm", b'')
            print(f"[SINK] packet from {user} len={len(pcm)}")
            # Only stop listening during actual TTS playback (self.playing).
            # Keep buffering while transcribing/thinking so we don't lose the
            # rest of the user's sentence and fragment their speech.
            if user is None or self.session.playing:
                return
            if pcm:
                self.session.feed(user.id, user.display_name, pcm)
        except Exception as e:
            print(f"[SINK] write error (skipping packet): {e}")

    def cleanup(self):
        pass


class VoiceSession:
    """Owns one voice connection and its conversation loop."""

    def __init__(self, bot, voice_client, text_channel, guild_id, owner_id=None):
        self.bot = bot
        self.vc = voice_client
        self.text_channel = text_channel
        self.guild_id = guild_id
        self.owner_id = owner_id   # only listen to the user who invited the bot

        self.speaking = False   # busy handling an utterance (pauses VAD flushing)
        self.playing = False    # TTS audio is actively playing (pauses listening)
        self._buffers = {}        # user_id -> bytearray
        self._names = {}          # user_id -> display_name
        self._last = {}           # user_id -> monotonic time of last packet
        self._resample_states = {}  # user_id -> audioop ratecv state
        self._lock = threading.Lock()
        self._task = None
        self._closed = False

    # ---- called from the recv thread ----
    def feed(self, user_id, name, pcm):
        """Buffer RAW 48kHz stereo PCM with a CONTINUOUS timeline.

        Critical: once a user starts talking we buffer EVERY packet, including
        the silent gaps between words. Dropping silent packets (e.g. Krisp emits
        true digital silence between words) splices the loud chunks together and
        every splice is a waveform discontinuity = a click. On a long sentence
        those stack into the 'static garbage' the audio turns into. So we only
        drop silence BEFORE speech has started (idle), never mid-utterance.
        """
        # Only listen to the user who invited the bot; ignore everyone else.
        if self.owner_id is not None and user_id != self.owner_id:
            return

        peak = 0
        if _NUMPY:
            arr = np.frombuffer(pcm, dtype=np.int16)
            if arr.size:
                peak = int(np.abs(arr.astype(np.int32)).max())

        with self._lock:
            already_active = bool(self._buffers.get(user_id))
            # Idle (no utterance in progress) + this packet is silence -> ignore,
            # so we don't accumulate dead air when nobody is speaking.
            if not already_active and peak < 10:
                return
            # Speech started or already going: keep the WHOLE stream, gaps included.
            self._buffers.setdefault(user_id, bytearray()).extend(pcm)
            self._names[user_id] = name
            # Only loud packets reset the silence timer that ends the utterance.
            if peak >= 10:
                self._last[user_id] = time.monotonic()

    # ---- async loop on the bot event loop ----
    def start(self):
        self._task = self.bot.loop.create_task(self._vad_loop())

    async def _vad_loop(self):
        while not self._closed:
            try:
                await asyncio.sleep(0.2)
                if self.speaking:
                    continue
                ready = None
                with self._lock:
                    now = time.monotonic()
                    for uid, buf in list(self._buffers.items()):
                        if buf:  # only log when there's actually something
                            silence = now - self._last.get(uid, 0)
                            print(f"[VAD] uid={uid} buf={len(buf)} silence={silence:.2f}s")
                        if buf and (now - self._last.get(uid, 0)) >= VOICE_SILENCE_SECONDS:
                            print(f"[VAD] FLUSHING uid={uid} buf={len(buf)} silence={silence:.2f}s")
                            ready = (uid, self._names.get(uid, "someone"), bytes(buf))
                            self._buffers[uid] = bytearray()
                            break
                if ready:
                    await self._handle_utterance(*ready)
            except Exception as e:
                print(f"[VAD] Loop error: {e}")

    async def _handle_utterance(self, user_id, name, raw48k):
        # Minimum buffer: 0.3s of raw 48kHz stereo 16-bit = 48000*0.3*2ch*2bytes
        MIN_BYTES = int(_IN_RATE * 0.3 * 2 * 2)
        if len(raw48k) < MIN_BYTES:
            print(f"[VOICE] Buffer too short ({len(raw48k)} bytes, <0.3s), skipping")
            return
        print(f"[TRANSCRIBE] raw48k bytes={len(raw48k)}, duration≈{len(raw48k)/_IN_RATE/4:.2f}s")
        if _DEBUG_DUMP:
            # Dump the RAW, unprocessed 48kHz stereo exactly as captured, so we can
            # tell whether corruption is upstream (Discord/decode) or in our cleanup.
            _rawpath = _debug_path(f"raw_{int(time.time())}.wav")
            with wave.open(_rawpath, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(_IN_RATE)
                wf.writeframes(raw48k)
            print(f"[DEBUG] saved raw capture -> {_rawpath}")
        try:
            self.speaking = True
            pcm16_processed = await asyncio.to_thread(_to_mono16k, raw48k)
            if not pcm16_processed:
                print("[VOICE] nothing usable after cleanup, skipping")
                return
            text = await asyncio.to_thread(transcribe, pcm16_processed, _TARGET_RATE)
            if not text:
                return
            print(f"[VOICE] {name}: {text}")

            system_prompt = get_system_prompt(user_id, self.guild_id, name)
            user_message = f"[{name}] (ID:{user_id}): {text}"
            reply = await generate_chat_with_nim(
                system_prompt=system_prompt,
                user_message=user_message,
                conversation_history=None,
            )
            if not reply or not reply.strip():
                return
            print(f"[VOICE] Scott: {reply}")

            wav = await asyncio.to_thread(synthesize, reply)
            if not wav:
                return
            await self._play_wav(wav)
        except Exception as e:
            print(f"[VOICE] utterance handling failed: {type(e).__name__}: {e}")
        finally:
            self.speaking = False

    async def _play_wav(self, wav_bytes):
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tmp.write(wav_bytes)
        tmp.close()
        try:
            done = asyncio.Event()

            def _after(err):
                if err:
                    print(f"[VOICE] playback error: {err}")
                self.bot.loop.call_soon_threadsafe(done.set)

            source = discord.FFmpegPCMAudio(tmp.name)
            if self.vc.is_playing():
                self.vc.stop()
            self.playing = True   # stop listening only while audio is playing
            self.vc.play(source, after=_after)
            await done.wait()
        finally:
            self.playing = False
            try:
                os.unlink(tmp.name)
            except OSError:
                pass

    async def close(self):
        self._closed = True
        if self._task:
            self._task.cancel()
        try:
            if self.vc and self.vc.is_connected():
                await self.vc.disconnect()
        except Exception:
            pass


# guild_id -> VoiceSession
_sessions = {}


def voice_recv_available() -> bool:
    return _RECV_AVAILABLE and _NUMPY


async def join_voice(bot, ctx) -> str:
    """Join the author's voice channel and start the conversation loop."""
    if not _RECV_AVAILABLE:
        return "Voice receive isn't installed. Run: `pip install discord-ext-voice-recv`"
    if not _NUMPY:
        return "numpy isn't installed. Run: `pip install numpy`"
    if not ctx.author.voice or not ctx.author.voice.channel:
        return "You need to be in a voice channel first."

    channel = ctx.author.voice.channel
    guild_id = ctx.guild.id

    if guild_id in _sessions:
        await _sessions[guild_id].close()
        _sessions.pop(guild_id, None)

    vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
    session = VoiceSession(bot, vc, ctx.channel, guild_id, owner_id=ctx.author.id)
    vc.listen(_ScottSink(session))
    session.start()
    _sessions[guild_id] = session

    async def _watchdog():
        """Restart the packet router if it dies from corrupted packets."""
        pass  # disabled for now - router patch handles restarts

    bot.loop.create_task(_watchdog())
    return None  # Silent join


async def say_text(ctx, text: str) -> str:
    """Test command: speak text via Magpie, skipping Whisper. Bot must be joined."""
    if not text or not text.strip():
        return "Usage: `!scott say [text]`"
    session = _sessions.get(ctx.guild.id)
    if not session:
        return "I'm not in a voice channel. Use `!scott join` first."
    wav = await asyncio.to_thread(synthesize, text)
    if not wav:
        return "TTS failed — check the console log."
    session.speaking = True
    try:
        await session._play_wav(wav)
    finally:
        session.speaking = False
    return ""


async def leave_voice(ctx) -> str:
    guild_id = ctx.guild.id
    session = _sessions.pop(guild_id, None)
    if not session:
        return "I'm not in a voice channel."
    await session.close()
    return "👋 Left the voice channel."
