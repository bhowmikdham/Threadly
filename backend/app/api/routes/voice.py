"""Voice proxy routes (module 9). ElevenLabs keys never leave the server;
payloads are PII-masked before egress (app.pii)."""
import base64
import os
import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentUser
from app.pii import mask_pii

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
        raise HTTPException(
            status_code=500,
            detail="ELEVENLABS_API_KEY is not configured on the backend server",
        )

    # 2. Mask sensitive user PII before sending payload off-server
    masked_text = mask_pii(req.text) if callable(mask_pii) else req.text

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "xi-api-key": api_key,
    }
    payload = {
        "text": masked_text,
        "model_id": "eleven_monolingual_v1",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75,
        },
    }

    # 3. Call ElevenLabs API
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers, timeout=15.0)

    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code,
            detail=f"ElevenLabs error: {response.text}",
        )

    # 4. Base64-encode MP3 bytes for VoiceOrb.tsx
    base64_audio = base64.b64encode(response.content).decode("utf-8")
    return {"audio": base64_audio}
