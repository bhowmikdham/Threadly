"""Module 9 — VOICE PROXY (build: W3).

STT/TTS passthrough to ElevenLabs. Keys live server-side ONLY — the extension
never sees them. Every outbound text payload goes through app.pii.masking
first (cloud egress rule).
"""


async def transcribe(audio_bytes: bytes, mime: str) -> str:
    """TODO(W3): ElevenLabs STT; enforce request size cap; return text."""
    raise NotImplementedError


async def synthesise(text: str):
    """TODO(W3): mask -> ElevenLabs TTS -> async byte stream (audio/mpeg)."""
    raise NotImplementedError
