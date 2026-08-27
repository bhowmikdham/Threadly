"""Voice proxy (module 9): STT/TTS passthrough to ElevenLabs. Keys live
server-side only — the extension never sees them. Dev mode without a key
returns deterministic stubs so the frontend can build the voice UI."""
import io
import struct

from fastapi import APIRouter, Depends, UploadFile
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from threadly_common.errors import APIError

from .. import clients, config, security

router = APIRouter()

ELEVENLABS_API = "https://api.elevenlabs.io"


def _require_key_or_dev() -> bool:
    """True = real ElevenLabs, False = dev stub. Raises when neither applies."""
    if config.ELEVENLABS_API_KEY:
        return True
    if config.DEV_MODE:
        return False
    raise APIError("voice_not_configured", "ELEVENLABS_API_KEY is not set.", status=501)


def _silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    frames = int(seconds * rate)
    data = b"\x00\x00" * frames
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + len(data), b"WAVE", b"fmt ", 16,
        1, 1, rate, rate * 2, 2, 16, b"data", len(data),
    )
    return header + data


class TTSRequest(BaseModel):
    text: str
    voice_id: str | None = None


@router.post("/voice/stt")
async def speech_to_text(audio: UploadFile, user_id: int = Depends(security.current_user)):
    if not _require_key_or_dev():
        return {"text": "[stub transcript] give me the order number of all the orders that i did for GYG", "stub": True}

    resp = await clients.http.post(
        f"{ELEVENLABS_API}/v1/speech-to-text",
        headers={"xi-api-key": config.ELEVENLABS_API_KEY},
        data={"model_id": "scribe_v1"},
        files={"file": (audio.filename or "audio", await audio.read(), audio.content_type or "audio/wav")},
    )
    if resp.status_code != 200:
        raise APIError("stt_failed", "Transcription failed.", status=502, details={"upstream": resp.text[:500]})
    return {"text": resp.json().get("text", ""), "stub": False}


@router.post("/voice/tts")
async def text_to_speech(body: TTSRequest, user_id: int = Depends(security.current_user)):
    if not _require_key_or_dev():
        return Response(content=_silent_wav(), media_type="audio/wav", headers={"X-Threadly-Stub": "true"})

    voice_id = body.voice_id or config.ELEVENLABS_VOICE_ID

    async def stream():
        async with clients.http.stream(
            "POST",
            f"{ELEVENLABS_API}/v1/text-to-speech/{voice_id}",
            headers={"xi-api-key": config.ELEVENLABS_API_KEY},
            json={"text": body.text, "model_id": "eleven_turbo_v2_5"},
        ) as resp:
            if resp.status_code != 200:
                raise APIError("tts_failed", "Speech synthesis failed.", status=502)
            async for chunk in resp.aiter_bytes():
                yield chunk

    return StreamingResponse(stream(), media_type="audio/mpeg")
