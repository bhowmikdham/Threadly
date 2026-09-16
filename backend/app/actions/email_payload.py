"""Pure plain-text MIME construction; no provider client or approval authority."""

import base64
import hashlib
import re
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime

from pydantic import ValidationError

from app.api.errors import ApiError
from app.schemas.assistant import DraftOptions
from app.schemas.draft_review import EditDraftRequest

SCHEMA = "email-mime-1.0.0"
_ATOM = r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+"
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
MESSAGE_ID = re.compile(rf"<{_ATOM}(?:\.{_ATOM})*@{_LABEL}(?:\.{_LABEL})*>")


def blocked(code):
    return ApiError(
        409, "email_preview_blocked", "Resolve the email preview blockers.", {"blockers": [code]}
    )


def reference_ids(values):
    if not isinstance(values, list) or len(values) > 1:
        raise blocked("reply_headers_invalid")
    if not values:
        return []
    value = values[0]
    if not isinstance(value, str) or len(value) > 8000:
        raise blocked("reply_headers_invalid")
    value = re.sub(r"\r\n[ \t]+", " ", value)
    if any(ord(c) < 32 and c != "\t" or ord(c) > 126 for c in value):
        raise blocked("reply_headers_invalid")
    ids = MESSAGE_ID.findall(value)
    if (
        not ids
        or len(ids) > 50
        or MESSAGE_ID.sub("", value).strip()
        or any(len(mid) > 254 or ".." in mid for mid in ids)
    ):
        raise blocked("reply_headers_invalid")
    return ids


def reply_headers(metadata, expected_id):
    if not isinstance(metadata, dict) or metadata.get("schema_version") != "1.0":
        raise blocked("reply_headers_unavailable")
    headers = metadata.get("headers")
    if not isinstance(headers, dict) or any(
        name not in headers for name in ("message-id", "references", "in-reply-to")
    ):
        raise blocked("reply_headers_unavailable")
    ids = reference_ids(headers["message-id"])
    if len(ids) != 1 or ids[0] != expected_id:
        raise blocked("reply_headers_invalid")
    refs = reference_ids(headers["references"])
    parents = reference_ids(headers["in-reply-to"])
    if not refs and len(parents) > 1:
        raise blocked("reply_headers_invalid")
    chain = refs or parents
    chain = list(dict.fromkeys([*chain, ids[0]]))
    if len(chain) > 50:
        raise blocked("reply_headers_invalid")
    return ids[0], chain


def build(envelope, content, *, sender, now, identifier, reply=None):
    if any(
        value.get(key)
        for value in (envelope, content)
        for key in ("attachments", "attachment_refs")
    ):
        raise blocked("attachments_unsupported")
    if set(envelope) - {"to", "cc", "bcc", "from_address", "reply_message_id", "reply"}:
        raise blocked("draft_envelope_invalid")
    if envelope.get("from_address") != sender:
        raise blocked("sender_changed")
    try:
        # Reuse explicit-edit validators to enforce the same recipient/text boundary.
        DraftOptions(to=[sender])
        draft = EditDraftRequest.model_validate(
            {
                "request_id": "payload-validation",
                "expected_revision": 1,
                "subject": content.get("subject"),
                "body": content.get("body"),
                "recipients": {field: envelope.get(field) for field in ("to", "cc", "bcc")},
                "unresolved_fields": content.get("unresolved_fields"),
            }
        )
    except ValidationError:
        raise blocked("draft_content_invalid") from None
    if not draft.recipients.to:
        raise blocked("recipients_required")
    if draft.unresolved_fields or re.search(r"\[[^\]\n]{1,200}\]|\{\{", draft.subject + draft.body):
        raise blocked("unresolved_fields")
    # Canonical LF body with one terminal newline, shown exactly in the preview.
    body = draft.body.replace("\r\n", "\n").replace("\r", "\n")
    if not body.endswith("\n"):
        body += "\n"
    message = EmailMessage(policy=policy.SMTP.clone(max_line_length=78))
    message["From"] = sender
    for field in ("to", "cc", "bcc"):
        addresses = getattr(draft.recipients, field)
        if addresses:
            message[field.title()] = ", ".join(addresses)
    message["Subject"] = draft.subject
    message["Date"] = format_datetime(now)
    domain = sender.rsplit("@", 1)[1]
    message["Message-ID"] = f"<{identifier}@{domain}>"
    if reply:
        message["In-Reply-To"] = reply["in_reply_to"]
        message["References"] = " ".join(reply["references"])
    message.set_content(body, subtype="plain", charset="utf-8", cte="base64")
    raw = message.as_bytes()
    if len(raw) > 64000:
        raise blocked("email_too_large")
    preview = {
        "from_address": sender,
        **draft.recipients.model_dump(exclude={"reply_message_id"}),
        "subject": draft.subject,
        "body": body,
        "date": str(message["Date"]),
        "message_id": str(message["Message-ID"]),
        "in_reply_to": reply["in_reply_to"] if reply else None,
        "references": reply["references"] if reply else [],
        "gmail_thread_id": reply["gmail_thread_id"] if reply else None,
    }
    return {
        "preview": preview,
        "mime_base64url": base64.urlsafe_b64encode(raw).decode("ascii"),
        "mime_sha256": hashlib.sha256(raw).hexdigest(),
    }
