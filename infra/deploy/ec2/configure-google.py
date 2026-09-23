#!/usr/bin/env python3
"""Run with sudo on EC2; prompt for the Google secret without echo or shell history."""

import argparse
import getpass
import os
import re
import shutil
import stat
import tempfile
import warnings
from pathlib import Path

ENV = Path("/srv/threadly-data/secrets/threadly.env")
CALLBACK = "http://127.0.0.1:8765/oauth/callback"


def update_env(path, client_id, secret):
    if not re.fullmatch(
        r"[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com", client_id
    ):
        raise ValueError("Expected a Google OAuth client ID.")
    # Current Google secrets use URL-safe characters. Refuse ambiguous dotenv syntax.
    if not re.fullmatch(r"[A-Za-z0-9._~-]{10,512}", secret):
        raise ValueError(
            "Secret format not supported; protected settings were not changed."
        )
    if path.is_symlink() or not path.is_file():
        raise ValueError("Protected environment file missing or unsafe.")
    info = path.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError(
            "Environment file must be owned by the current user with mode 600."
        )
    updates = {
        "GOOGLE_CLIENT_ID": client_id,
        "GOOGLE_CLIENT_SECRET": secret,
        "GOOGLE_REDIRECT_URI": CALLBACK,
        "GOOGLE_ALLOW_LOOPBACK_TEST_CALLBACK": "true",
        "EMAIL_WRITES_ENABLED": "false",
        "CALENDAR_WRITES_ENABLED": "false",
        "WRITE_PILOT_USER_IDS": "",
    }
    pattern = r"^\s*(?:export\s+)?(" + "|".join(updates) + r")\s*="
    lines = [
        line for line in path.read_text().splitlines() if not re.match(pattern, line)
    ]
    lines += [f"{key}={value}" for key, value in updates.items()]
    fd, backup = tempfile.mkstemp(
        prefix="before-google-", suffix=".env", dir=path.parent
    )
    os.close(fd)
    shutil.copyfile(path, backup)
    fd, pending = tempfile.mkstemp(prefix=".google-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(pending, path)
    finally:
        if os.path.exists(pending):
            os.unlink(pending)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client-id", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or not Path("/opt/threadly/BOOTSTRAP_READY").is_file():
        raise SystemExit("Run with sudo on the bootstrapped Threadly EC2 host.")
    # Do not race deployment or a second configuration editor.
    import fcntl

    with open("/var/lock/threadly-deploy.lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another deployment/configuration is running.") from None
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                secret = getpass.getpass("Google client secret (hidden): ")
                backup = update_env(ENV, args.client_id, secret)
            except (getpass.GetPassWarning, EOFError):
                raise SystemExit(
                    "Use an interactive Session Manager terminal; input not saved."
                ) from None
            except ValueError as exc:
                raise SystemExit(str(exc)) from None
    print("GOOGLE_SETTINGS_SAVED; private backup:", backup)
    print("Restart all application services to load settings, then run laptop login.")
    print("External writes remain disabled. No Google request was made.")


if __name__ == "__main__":
    main()
