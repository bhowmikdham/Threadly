"""Production must never start with the development JWT signing key."""

import secrets

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_rejects_default_and_short_session_keys():
    default_key = Settings.model_fields["secret_key"].default
    for key in (default_key, "too-short"):
        with pytest.raises(ValidationError, match="non-default SECRET_KEY"):
            Settings(_env_file=None, app_env="prod", secret_key=key)


def test_development_default_and_ec2_generated_production_key_are_valid():
    default_key = Settings.model_fields["secret_key"].default
    assert Settings(_env_file=None, app_env="dev", secret_key=default_key).secret_key == default_key
    key = secrets.token_hex(32)
    assert Settings(_env_file=None, app_env="prod", secret_key=key).secret_key == key
