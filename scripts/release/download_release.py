#!/usr/bin/env python3
"""Build and install the public extension ZIP. Uses only Python's standard library."""

import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import urllib.request
import zipfile

REPOSITORY = "bhowmikdham/Threadly"
ORIGIN = "https://api.threadly.au"
PUBLIC_URL = "https://threadly.au/downloads/threadly-extension.zip"
FEED_URL = f"https://github.com/{REPOSITORY}/releases/download/extension-download-current/release.json"
HEAD_URL = f"https://api.github.com/repos/{REPOSITORY}/git/ref/heads/frontend"
MAX_ZIP = 20 * 1024 * 1024
MAX_UNPACKED = 80 * 1024 * 1024
DEFAULT_ROOT = Path("/srv/threadly-data")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version_parts(version):
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){0,3}", version):
        raise ValueError("Invalid manifest version")
    parts = tuple(int(part) for part in version.split("."))
    return parts + (0,) * (4 - len(parts))


def validate_metadata(meta):
    if not isinstance(meta, dict) or meta.get("schema") != 1:
        raise ValueError("Unsupported release metadata")
    for field, pattern in (
        ("commit", r"[0-9a-f]{40}"),
        ("sha256", r"[0-9a-f]{64}"),
        ("version", r"\d+(?:\.\d+){0,3}"),
    ):
        if not isinstance(meta.get(field), str) or not re.fullmatch(pattern, meta[field]):
            raise ValueError(f"Invalid {field}")
    for field in ("run_id", "run_attempt", "bytes"):
        if type(meta.get(field)) is not int or meta[field] <= 0:
            raise ValueError(f"Invalid {field}")
    if meta["bytes"] > MAX_ZIP or meta.get("backend_origin") != ORIGIN:
        raise ValueError("Wrong backend origin or oversized release")
    if meta.get("repository") != REPOSITORY:
        raise ValueError("Wrong release repository")
    return meta


def release_tag(meta):
    return f"extension-download-{meta['run_id']}-{meta['run_attempt']}"


def package_url(meta):
    return f"https://github.com/{REPOSITORY}/releases/download/{release_tag(meta)}/threadly-extension.zip"


def inspect_zip(data, expected_version=None):
    if len(data) > MAX_ZIP:
        raise ValueError("ZIP is too large")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(names) != len(set(names)) or len(names) > 2000:
            raise ValueError("Duplicate ZIP entries or too many files")
        if sum(entry.file_size for entry in entries) > MAX_UNPACKED:
            raise ValueError("Unpacked ZIP is too large")
        for entry in entries:
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or ".." in path.parts or "\\" in entry.filename:
                raise ValueError("Unsafe ZIP path")
            if stat.S_ISLNK(entry.external_attr >> 16) or entry.flag_bits & 1:
                raise ValueError("Symlink or encrypted ZIP entry")
        if archive.testzip() is not None:
            raise ValueError("Corrupt ZIP")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("manifest_version") != 3 or not manifest.get("key"):
            raise ValueError("Expected the unpacked MV3 manifest with stable identity")
        if expected_version is not None and manifest.get("version") != expected_version:
            raise ValueError("Manifest version differs from release metadata")
        required = [manifest.get("side_panel", {}).get("default_path"),
                    manifest.get("background", {}).get("service_worker")]
        if any(not name or name not in names for name in required):
            raise ValueError("Missing side panel or background worker")
        if not any(ORIGIN.encode() in archive.read(name) for name in names if name.endswith(".js")):
            raise ValueError("Public API origin missing from compiled JavaScript")
        return manifest


def package(build_dir, output_dir, commit, run_id, run_attempt):
    build_dir, output_dir = Path(build_dir), Path(output_dir)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(build_dir.rglob("*")):
            if path.is_symlink():
                raise ValueError("Build contains a symlink")
            if path.is_file():
                entry = zipfile.ZipInfo(path.relative_to(build_dir).as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(entry, path.read_bytes())
    data = buffer.getvalue()
    manifest = inspect_zip(data)
    meta = validate_metadata({
        "schema": 1, "repository": REPOSITORY, "commit": commit,
        "run_id": int(run_id), "run_attempt": int(run_attempt),
        "version": manifest["version"], "backend_origin": ORIGIN,
        "sha256": digest(data), "bytes": len(data),
    })
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "threadly-extension.zip").write_bytes(data)
    (output_dir / "release.json").write_text(json.dumps(meta, indent=2) + "\n")
    return meta


def fetch(url, limit):
    request = urllib.request.Request(url, headers={
        "User-Agent": "Threadly-extension-download-updater/1",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(request, timeout=30) as response:
        if not response.geturl().startswith("https://"):
            raise ValueError("Download must use HTTPS")
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Download exceeded size limit")
    return data


def atomic_write(path, data, mode=0o644):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def encode(meta):
    return (json.dumps(meta, indent=2) + "\n").encode()


def read_json(path):
    return json.loads(path.read_bytes()) if path.exists() else None


def update(root=DEFAULT_ROOT, get=fetch):
    root = Path(root)
    state = root / "extension-releases"
    public = root / "public-downloads" / "threadly-extension.zip"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "update.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return update_locked(state, public, get)


def update_locked(state, public, get):
    if not public.is_file() or public.is_symlink():
        raise ValueError("Existing public ZIP is missing or a symlink")
    current_path = state / "current.json"
    pending_path = state / "pending.json"
    previous_zip = state / "previous.zip"
    # Recover an interrupted swap before fetching any new remote state.
    pending = read_json(pending_path)
    if pending:
        atomic_write(public, previous_zip.read_bytes())
        if pending["previous"] is None:
            current_path.unlink(missing_ok=True)
        else:
            atomic_write(current_path, encode(pending["previous"]))
        pending_path.unlink()
    meta = validate_metadata(json.loads(get(FEED_URL, 16384)))
    old_data = public.read_bytes()
    old_manifest = inspect_zip(old_data)
    if version_parts(meta["version"]) < version_parts(old_manifest["version"]):
        return "unchanged: website already has a newer extension version"
    current = read_json(current_path)
    identity = (meta["run_id"], meta["run_attempt"])
    if current:
        validate_metadata(current)
        current_identity = (current["run_id"], current["run_attempt"])
        if identity < current_identity:
            raise ValueError("Refusing an older workflow run")
        if identity == current_identity:
            if meta != current or digest(public.read_bytes()) != meta["sha256"]:
                raise ValueError("Published release changed without a new workflow run")
            return "unchanged"
    # Prevent delayed or rerun builds from replacing the latest frontend release.
    def check_head():
        head = json.loads(get(HEAD_URL, 16384))["object"]["sha"]
        if head != meta["commit"]:
            raise ValueError("Release is not the current frontend commit")

    check_head()
    data = get(package_url(meta), MAX_ZIP)
    if len(data) != meta["bytes"] or digest(data) != meta["sha256"]:
        raise ValueError("Release checksum or size mismatch")
    inspect_zip(data, meta["version"])
    check_head()
    atomic_write(previous_zip, old_data)
    atomic_write(state / "previous.json", encode(current))
    atomic_write(pending_path, encode({"previous": current, "candidate": meta}))
    try:
        atomic_write(public, data)
        served = get(f"{PUBLIC_URL}?release={meta['sha256']}", MAX_ZIP)
        if digest(served) != meta["sha256"]:
            raise ValueError("Website download verification failed")
        atomic_write(current_path, encode(meta))
        pending_path.unlink()
    except BaseException:
        atomic_write(public, old_data)
        if current is None:
            current_path.unlink(missing_ok=True)
        else:
            atomic_write(current_path, encode(current))
        pending_path.unlink(missing_ok=True)
        raise
    return f"published {meta['version']} ({meta['commit']})"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("package")
    build.add_argument("--build-dir", default="build/chrome-mv3-prod")
    build.add_argument("--output-dir", default="build/website-release")
    build.add_argument("--commit", required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--run-attempt", required=True)
    commands.add_parser("update")
    args = parser.parse_args()
    if args.command == "package":
        print(json.dumps(package(args.build_dir, args.output_dir, args.commit,
                                 args.run_id, args.run_attempt)))
    else:
        print(update())


if __name__ == "__main__":
    main()
