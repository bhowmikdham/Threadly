"""Owned meeting offer revisions and durable selection checks.

Revision ID: e14026e9a034
Revises: d13026e9a033
"""

import sqlalchemy as sa

from alembic import op

revision = "e14026e9a034"
down_revision = "d13026e9a033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_thread_owner", "threads", ["id", "user_id"])
    op.execute("""
    CREATE TABLE meeting_negotiations (
    id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    thread_id INTEGER NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    policy_version VARCHAR(80) NOT NULL,
    version INTEGER NOT NULL,
    state VARCHAR(20) NOT NULL,
    current_offer_id VARCHAR(36),
    current_selection_id VARCHAR(36),
    close_request_id VARCHAR(128),
    close_request_hash VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_owner UNIQUE (id, user_id),
    CONSTRAINT uq_meeting_request UNIQUE (user_id, request_id),
    CONSTRAINT fk_meeting_thread_owner FOREIGN KEY(thread_id, user_id) REFERENCES threads (id,
    user_id) ON DELETE CASCADE,
    CONSTRAINT ck_meeting_version CHECK (version >= 1),
    CONSTRAINT ck_meeting_offer_pointer CHECK ((state='open' AND current_offer_id IS NULL AND
    current_selection_id IS NULL) OR (state IN ('offered','checking','selected') AND
    current_offer_id IS NOT NULL) OR state='closed'),
    CONSTRAINT ck_meeting_selection_pointer CHECK (state NOT IN ('checking','selected') OR
    current_selection_id IS NOT NULL),
    CONSTRAINT ck_meeting_close_receipt CHECK ((state='closed' AND close_request_id IS NOT
    NULL AND close_request_hash IS NOT NULL) OR (state!='closed' AND close_request_id IS NULL
    AND close_request_hash IS NULL)),
    CONSTRAINT ck_meeting_state CHECK (state IN
    ('open','offered','checking','selected','closed')),
    FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE INDEX ix_meeting_negotiations_user_id ON meeting_negotiations (user_id)
    """)
    op.execute("""
    CREATE TABLE meeting_offers (
    id VARCHAR(36) NOT NULL,
    negotiation_id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    revision INTEGER NOT NULL,
    created_version INTEGER NOT NULL,
    thread_version INTEGER NOT NULL,
    slot_request_id VARCHAR(36) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_offer_owner UNIQUE (id, negotiation_id, user_id),
    CONSTRAINT uq_meeting_offer_request UNIQUE (negotiation_id, request_id),
    CONSTRAINT uq_meeting_offer_revision UNIQUE (negotiation_id, revision),
    CONSTRAINT fk_meeting_offer_negotiation FOREIGN KEY(negotiation_id, user_id) REFERENCES
    meeting_negotiations (id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_meeting_offer_slots FOREIGN KEY(slot_request_id, user_id) REFERENCES
    calendar_slot_requests (id, user_id),
    CONSTRAINT ck_meeting_offer_versions CHECK (revision >= 1 AND created_version >= 2 AND
    thread_version >= 0 AND expires_at > created_at)
    )
    """)
    op.execute("""
    CREATE INDEX ix_meeting_offers_negotiation_id ON meeting_offers (negotiation_id)
    """)
    op.execute("""
    CREATE TABLE meeting_selections (
    id VARCHAR(36) NOT NULL,
    negotiation_id VARCHAR(36) NOT NULL,
    user_id INTEGER NOT NULL,
    offer_id VARCHAR(36) NOT NULL,
    request_id VARCHAR(128) NOT NULL,
    request_hash VARCHAR(64) NOT NULL,
    created_version INTEGER NOT NULL,
    slot_id VARCHAR(36) NOT NULL,
    state VARCHAR(20) NOT NULL,
    checked_slot_request_id VARCHAR(36),
    error_code VARCHAR(80),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL,
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_meeting_selection_owner UNIQUE (id, negotiation_id, user_id),
    CONSTRAINT uq_meeting_selection_request UNIQUE (negotiation_id, request_id),
    CONSTRAINT fk_meeting_selection_negotiation FOREIGN KEY(negotiation_id, user_id)
    REFERENCES meeting_negotiations (id, user_id) ON DELETE CASCADE,
    CONSTRAINT fk_meeting_selection_offer FOREIGN KEY(offer_id, negotiation_id, user_id)
    REFERENCES meeting_offers (id, negotiation_id, user_id),
    CONSTRAINT fk_meeting_selection_check FOREIGN KEY(checked_slot_request_id, user_id)
    REFERENCES calendar_slot_requests (id, user_id),
    CONSTRAINT ck_meeting_selection_versions CHECK (created_version >= 3 AND expires_at >
    created_at),
    CONSTRAINT ck_meeting_selection_state CHECK (state IN
    ('checking','selected','conflict','unknown','failed','superseded'))
    )
    """)
    op.execute("""
    CREATE INDEX ix_meeting_selections_negotiation_id ON meeting_selections (negotiation_id)
    """)
    op.execute("""
    ALTER TABLE meeting_negotiations ADD CONSTRAINT fk_meeting_current_offer FOREIGN
    KEY(current_offer_id, id, user_id) REFERENCES meeting_offers (id, negotiation_id, user_id)
    """)
    op.execute("""
    ALTER TABLE meeting_negotiations ADD CONSTRAINT fk_meeting_current_selection FOREIGN
    KEY(current_selection_id, id, user_id) REFERENCES meeting_selections (id, negotiation_id,
    user_id)
    """)
    op.execute("""CREATE FUNCTION guard_meeting_negotiation() RETURNS trigger AS $$
      BEGIN
        IF OLD.state = 'closed' OR NEW.version != OLD.version + 1
           OR (to_jsonb(NEW) - ARRAY['version','state','current_offer_id','current_selection_id',
                                     'close_request_id','close_request_hash']) IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['version','state','current_offer_id','current_selection_id',
                                     'close_request_id','close_request_hash']) THEN
          RAISE EXCEPTION 'Meeting changes require a new version and immutable identity'
            USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER meeting_negotiation_guard BEFORE UPDATE ON meeting_negotiations
      FOR EACH ROW EXECUTE FUNCTION guard_meeting_negotiation()""")
    op.execute("""CREATE FUNCTION guard_meeting_offer() RETURNS trigger AS $$
      BEGIN
        RAISE EXCEPTION 'Meeting offers are immutable' USING ERRCODE='23514';
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER meeting_offer_guard BEFORE UPDATE ON meeting_offers
      FOR EACH ROW EXECUTE FUNCTION guard_meeting_offer()""")
    op.execute("""CREATE FUNCTION guard_meeting_selection() RETURNS trigger AS $$
      BEGIN
        IF OLD.state != 'checking' OR NEW.state = 'checking'
           OR (to_jsonb(NEW) - ARRAY['state','checked_slot_request_id','error_code','expires_at'])
              IS DISTINCT FROM
              (to_jsonb(OLD) - ARRAY['state','checked_slot_request_id','error_code','expires_at'])
           OR NEW.expires_at > OLD.expires_at THEN
          RAISE EXCEPTION 'Selection changes require immutable terminal publication'
            USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END; $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER meeting_selection_guard BEFORE UPDATE ON meeting_selections
      FOR EACH ROW EXECUTE FUNCTION guard_meeting_selection()""")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT EXISTS(SELECT 1 FROM meeting_negotiations)")).scalar():
        raise RuntimeError("Cannot downgrade while meeting negotiations exist")
    op.drop_constraint("fk_meeting_current_offer", "meeting_negotiations", type_="foreignkey")
    op.drop_constraint("fk_meeting_current_selection", "meeting_negotiations", type_="foreignkey")
    op.drop_table("meeting_selections")
    op.drop_table("meeting_offers")
    op.drop_table("meeting_negotiations")
    op.execute("DROP FUNCTION guard_meeting_selection()")
    op.execute("DROP FUNCTION guard_meeting_offer()")
    op.execute("DROP FUNCTION guard_meeting_negotiation()")
    op.drop_constraint("uq_thread_owner", "threads", type_="unique")
