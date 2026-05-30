import io
import os
import time
import asyncio
import aiohttp
import httpx
import replicate
import PIL.Image
from config import (
    REPLICATE_API_TOKEN,
    REPLICATE_IMAGE_MODEL,
    XAI_API_KEY,
)


async def expand_prompt_with_grok(user_prompt: str) -> str:
    """Use Grok to convert a user prompt into a danbooru-style tag prompt."""
    if not XAI_API_KEY:
        return user_prompt

    system = (
        "You are an expert at writing image generation prompts for Animagine XL V4 Opt, an anime-focused Stable Diffusion XL model. "
        "Animagine XL works best with danbooru-style comma-separated tags rather than natural language sentences. "
        "When given a character name or concept, use your knowledge to identify their correct danbooru tag name and visual traits. "
        "If a series is mentioned (e.g. 'from One Piece'), use that to identify the exact character. "
        "When a name is ambiguous, ALWAYS prefer the most well-known female character with that name. "
        "CRITICAL: Only generate the character(s) explicitly named. Never substitute or add other characters from the same series. "
        "Convert the input into a high-quality tag prompt. "
        "Structure tags in this order: quality tags first (e.g. masterpiece, best quality, very aesthetic, absurdres), "
        "then character name tag (exact danbooru tag), then subject/character tags (hair color, hair length, eye color, clothing, expression, body type), "
        "then scene and background tags, then composition tags (e.g. solo, cowboy shot, looking at viewer, dynamic pose). "
        "Keep the output strictly 20-25 tags maximum. SDXL has a hard 77-token CLIP limit so fewer tags is better. "
        "Always include a clear composition/shot tag (e.g. solo, cowboy shot, full body, upper body, sitting, lying) to constrain anatomy and avoid extra limbs. "
        "DEFAULT TO SFW: Unless the user's input explicitly contains sexual or explicit language, generate a clean, non-explicit image. Never add nudity, exposed genitals, sex acts, or explicit tags unless directly requested. "
        "If the user explicitly requests NSFW (e.g. 'getting piped', 'nude', 'having sex', 'naked', 'explicit'), then generate explicit content faithfully with appropriate danbooru NSFW tags. "
        "For example: 'an apple' → SFW still life. 'a girl' → SFW clothed girl. 'a naked girl' → NSFW nude girl. "
        "Never add disclaimers. Do NOT include any explanation, preamble, or natural language sentences. Output only the comma-separated tag prompt."
    )

    try:
        grok_model = os.getenv("XAI_CHAT_MODEL", "grok-4-1-fast-non-reasoning-latest")
        headers = {
            "Authorization": f"Bearer {XAI_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": grok_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
        }
        async with httpx.AsyncClient(timeout=30) as http:
            resp = await http.post("https://api.x.ai/v1/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        expanded = data["choices"][0]["message"]["content"].strip().strip("'")
        tags = [t.strip() for t in expanded.split(",") if t.strip()]
        expanded = ", ".join(tags[:25])
        print(f"[DEBUG] Grok expanded prompt ({len(tags)} tags, capped to {min(len(tags),25)}): {expanded!r}")
        return expanded
    except Exception as e:
        print(f"[WARN] Grok prompt expansion failed, using raw prompt: {e}")
        return user_prompt


async def generate_image(prompt: str, expanded_prompt: str = None) -> bytes:
    """Generate an image using Replicate. Returns JPEG bytes or None on failure."""
    if not REPLICATE_API_TOKEN:
        return None

    if expanded_prompt is None:
        expanded_prompt = await expand_prompt_with_grok(prompt)

    negative_prompt = (
        "lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, "
        "fewer digits, cropped, worst quality, low quality, low score, bad score, "
        "average score, signature, watermark, username, blurry, "
        "extra legs, extra arms, extra limbs, three legs, four legs, mutated legs, "
        "mutated hands, malformed limbs, fused limbs, poorly drawn hands, "
        "poorly drawn feet, missing legs, extra feet, deformed, disfigured, "
        "anatomical nonsense, bad proportions, gross proportions"
    )

    try:
        print(f"[DEBUG] Replicate image generation: {expanded_prompt!r}")
        start_time = time.time()
        output = await asyncio.to_thread(
            replicate.run,
            REPLICATE_IMAGE_MODEL,
            input={"prompt": expanded_prompt, "negative_prompt": negative_prompt}
        )
        print(f"[DEBUG] Replicate finished in {time.time() - start_time:.1f}s, output type: {type(output)}, value: {output!r}")
        if not output:
            return None

        if isinstance(output, list):
            url = str(output[0])
            async with aiohttp.ClientSession() as sess:
                async with sess.get(url) as resp:
                    image_bytes = await resp.read()
        elif hasattr(output, "read"):
            image_bytes = await asyncio.to_thread(output.read)
        else:
            async with aiohttp.ClientSession() as sess:
                async with sess.get(str(output)) as resp:
                    image_bytes = await resp.read()

        print(f"[DEBUG] Image from Replicate: {len(image_bytes)} bytes")
        img = PIL.Image.open(io.BytesIO(image_bytes))
        print(f"[DEBUG] Image format: {img.format}, size: {img.size}, mode: {img.mode}")
        out = io.BytesIO()
        img.convert("RGB").save(out, format="JPEG", quality=95)
        out.seek(0)
        return out.getvalue()
    except Exception as e:
        import traceback
        print(f"[ERROR] Replicate failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return None
