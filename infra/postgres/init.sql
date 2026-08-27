-- Threadly schema. One postgres instance; logical ownership per service is
-- documented in contracts/CONTRACTS.md (§ data ownership).

CREATE TABLE users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    google_sub  TEXT UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE oauth_tokens (
    user_id            BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provider           TEXT NOT NULL DEFAULT 'google',
    enc_refresh_token  TEXT,
    enc_access_token   TEXT,
    access_expires_at  TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, provider)
);

CREATE TABLE threads (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    gmail_thread_id  TEXT NOT NULL,
    subject          TEXT,
    last_msg_at      TIMESTAMPTZ,
    msg_count        INT NOT NULL DEFAULT 0,
    UNIQUE (user_id, gmail_thread_id)
);
CREATE INDEX threads_recent_idx ON threads (user_id, last_msg_at DESC);

CREATE TABLE messages (
    id            BIGSERIAL PRIMARY KEY,
    user_id       BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id     BIGINT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    gmail_msg_id  TEXT NOT NULL,
    from_addr     TEXT,
    to_addrs      TEXT[] NOT NULL DEFAULT '{}',
    is_sent       BOOLEAN NOT NULL DEFAULT FALSE,   -- authored by the user (RAG source)
    sent_at       TIMESTAMPTZ,
    subject       TEXT,
    snippet       TEXT,
    body_text     TEXT,
    tsv           tsvector GENERATED ALWAYS AS
                  (to_tsvector('english', coalesce(subject, '') || ' ' || coalesce(snippet, '') || ' ' || coalesce(body_text, ''))) STORED,
    UNIQUE (user_id, gmail_msg_id)
);
CREATE INDEX messages_fts_idx ON messages USING GIN (tsv);
CREATE INDEX messages_thread_idx ON messages (thread_id, sent_at);

-- Summary cache. Cache key: (thread_id, last_msg_id) — a new message in the
-- thread changes last_msg_id, which invalidates by simply missing the cache.
CREATE TABLE summaries (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id    BIGINT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    last_msg_id  TEXT NOT NULL,
    model        TEXT,
    text         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, last_msg_id)
);

-- Structured facts extracted at sync time. Answering "all my GYG order
-- numbers" is a SQL query here — structured data goes around the LLM.
CREATE TABLE entities (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type           TEXT NOT NULL,              -- order | tracking | amount | ...
    key            TEXT NOT NULL,              -- the value itself, e.g. "GYG-84721"
    value          JSONB NOT NULL DEFAULT '{}',
    merchant       TEXT,                       -- derived from sender domain
    source_msg_id  TEXT NOT NULL,
    occurred_at    TIMESTAMPTZ NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, type, key)
);
CREATE INDEX entities_browse_idx ON entities (user_id, type, occurred_at DESC, id DESC);
CREATE INDEX entities_merchant_idx ON entities (user_id, merchant);

CREATE TABLE commitments (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    description    TEXT NOT NULL,
    due_at         TIMESTAMPTZ,
    source_msg_id  TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'open',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, source_msg_id, description)
);

-- Draft lifecycle: generated -> edited -> approved -> sending -> sent | failed.
-- The LLM writes rows here; only POST /v1/drafts/{id}/send (user-gated) can
-- turn one into an outbound email.
CREATE TABLE drafts (
    id               BIGSERIAL PRIMARY KEY,
    user_id          BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    thread_id        BIGINT REFERENCES threads(id) ON DELETE SET NULL,
    intent           TEXT,
    to_addrs         TEXT[] NOT NULL DEFAULT '{}',
    subject          TEXT,
    body             TEXT,
    status           TEXT NOT NULL DEFAULT 'generated',
    model            TEXT,
    gmail_msg_id     TEXT,
    idempotency_key  TEXT,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX drafts_idempotency_idx
    ON drafts (user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX drafts_list_idx ON drafts (user_id, created_at DESC);
