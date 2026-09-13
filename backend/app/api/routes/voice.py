"""Voice proxy routes (module 9). ElevenLabs keys never leave the server;
payloads are PII-masked before egress (app.pii)."""
from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.api.errors import not_implemented

router = APIRouter()


@router.post("/transcribe")
async def transcribe(user_id: CurrentUser) -> dict:
    # W3: multipart audio -> ElevenLabs STT -> {"text"}
    raise not_implemented("Voice transcription", "W3")


@router.post("/speak")
async def speak(user_id: CurrentUser):
    # W3: {"text"} -> masked -> ElevenLabs TTS -> audio/mpeg stream
    raise not_implemented("Voice synthesis", "W3")
