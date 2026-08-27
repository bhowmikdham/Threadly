import pytest
from threadly_common.cursor import decode_cursor, encode_cursor


def test_roundtrip():
    cursor = encode_cursor("2026-08-01T10:00:00+00:00", 42)
    assert decode_cursor(cursor) == ("2026-08-01T10:00:00+00:00", 42)


def test_opaque_garbage_rejected():
    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor!!")
    with pytest.raises(ValueError):
        decode_cursor("")
