"""Voice proxy routes (module 9). ElevenLabs keys never leave the server;
payloads are PII-masked before egress (app.pii)."""

import base64
import os

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.api.errors import ApiError
from app.pii.masking import mask

router = APIRouter()


class SpeakRequest(BaseModel):
    text: str


@router.post("/transcribe")
async def transcribe(user_id: CurrentUser) -> dict:
    # Reserved for future STT audio upload implementation
    return {"text": ""}


@router.post("/speak")
async def speak(user_id: CurrentUser, req: SpeakRequest):
    # 1. Fetch secret key safely on EC2 server
    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

    if not api_key:
        raise ApiError(
            503,
            "voice_unavailable",
            "Speech is unavailable right now. Try again later.",
        )

    # 2. Mask sensitive user PII before sending payload off-server
    masked_text, _ = mask(req.text)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    payload = {
        "text": masked_text,
        "model_id": "eleven_flash_v2_5",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    # 3. Call ElevenLabs API
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=15.0)
    except httpx.TimeoutException as exc:
        raise ApiError(504, "voice_timeout", "Speech took too long. Try again later.") from exc
    except httpx.RequestError as exc:
        raise ApiError(
            503,
            "voice_unavailable",
            "Speech is unavailable right now. Try again later.",
        ) from exc

    if response.status_code != 200:
        # Provider credentials/quota belong to this service, not the user's
        # Threadly session or Google grant. Only CurrentUser may reject auth.
        # Do not forward provider bodies, which may contain private diagnostics.
        raise ApiError(
            502,
            "voice_provider_error",
            "Speech is unavailable right now. Try again later.",
        )

    # 4. Base64-encode MP3 bytes for VoiceOrb.tsx
    base64_audio = base64.b64encode(response.content).decode("utf-8")
    return {"audio": base64_audio}
