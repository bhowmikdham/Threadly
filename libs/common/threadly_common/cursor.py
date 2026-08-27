"""Opaque keyset-pagination cursors for progressive disclosure ("want more?")."""
import base64
import json


def encode_cursor(occurred_at_iso: str, row_id: int) -> str:
    payload = json.dumps({"t": occurred_at_iso, "id": row_id}).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_cursor(cursor: str) -> tuple[str, int]:
    try:
        obj = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return str(obj["t"]), int(obj["id"])
    except Exception as exc:
        raise ValueError("invalid cursor") from exc
