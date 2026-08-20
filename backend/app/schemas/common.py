"""Envelope + shared shapes. These mirror docs/api-contract.md — change both in one PR."""
from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: object | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody
