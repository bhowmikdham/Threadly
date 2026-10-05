import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import warnings

import download_release as release


class DownloadReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.build = self.root / "build"
        self.build.mkdir()
        manifest = {
            "manifest_version": 3, "version": "0.2.0", "key": "stable-public-key",
            "side_panel": {"default_path": "sidepanel.html"},
            "background": {"service_worker": "background.js"},
        }
        (self.build / "manifest.json").write_text(json.dumps(manifest))
        (self.build / "sidepanel.html").write_text("<html>Threadly</html>")
        (self.build / "background.js").write_text(f'const origin = "{release.ORIGIN}"')
        self.meta = release.package(self.build, self.root / "output", "a" * 40, 100, 1)
        self.data = (self.root / "output/threadly-extension.zip").read_bytes()
        manifest["version"] = "0.1.0"
        (self.build / "manifest.json").write_text(json.dumps(manifest))
        release.package(self.build, self.root / "old", "b" * 40, 99, 1)
        self.old_data = (self.root / "old/threadly-extension.zip").read_bytes()
        manifest["version"] = "0.2.0"
        (self.build / "manifest.json").write_text(json.dumps(manifest))
        self.public = self.root / "public-downloads/threadly-extension.zip"
        self.public.parent.mkdir()
        self.public.write_bytes(self.old_data)
        self.state = self.root / "extension-releases"
        self.calls = []

    def get(self, url, limit):
        self.calls.append(url)
        if url == release.FEED_URL:
            return release.encode(self.meta)
        if url == release.HEAD_URL:
            return release.encode({"object": {"sha": self.meta["commit"]}})
        if url == release.package_url(self.meta):
            return self.data
        if url.startswith(release.PUBLIC_URL + "?"):
            return self.public.read_bytes()
        raise AssertionError(f"Unexpected URL {url}")

    def test_package_is_reproducible_and_rooted_at_manifest(self):
        second = release.package(self.build, self.root / "output2", "a" * 40, 100, 1)
        self.assertEqual(second, self.meta)
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            self.assertEqual(archive.read("background.js"), (self.build / "background.js").read_bytes())
        self.assertEqual(release.digest(self.data), self.meta["sha256"])

    def test_publishes_exact_bytes_and_preserves_previous_download(self):
        self.assertIn("published", release.update(self.root, self.get))
        self.assertEqual(self.public.read_bytes(), self.data)
        self.assertEqual((self.state / "previous.zip").read_bytes(), self.old_data)
        self.assertEqual(release.read_json(self.state / "current.json"), self.meta)
        self.assertFalse((self.state / "pending.json").exists())
        self.assertEqual(self.public.stat().st_mode & 0o777, 0o644)

    def test_unchanged_release_only_reads_feed(self):
        release.update(self.root, self.get)
        self.calls.clear()
        self.assertEqual(release.update(self.root, self.get), "unchanged")
        self.assertEqual(self.calls, [release.FEED_URL])

    def test_checksum_mismatch_leaves_old_download(self):
        self.data += b"tampered"
        with self.assertRaisesRegex(ValueError, "checksum or size"):
            release.update(self.root, self.get)
        self.assertEqual(self.public.read_bytes(), self.old_data)

    def test_stale_commit_never_publishes(self):
        def get(url, limit):
            if url == release.HEAD_URL:
                return release.encode({"object": {"sha": "b" * 40}})
            return self.get(url, limit)
        with self.assertRaisesRegex(ValueError, "current frontend"):
            release.update(self.root, get)
        self.assertEqual(self.public.read_bytes(), self.old_data)

    def test_commit_advancing_during_download_does_not_publish(self):
        head_checks = 0
        def get(url, limit):
            nonlocal head_checks
            if url == release.HEAD_URL:
                head_checks += 1
                return release.encode({"object": {"sha": self.meta["commit"] if head_checks == 1 else "b" * 40}})
            return self.get(url, limit)
        with self.assertRaisesRegex(ValueError, "current frontend"):
            release.update(self.root, get)
        self.assertEqual(self.public.read_bytes(), self.old_data)

    def test_failed_public_check_rolls_back_bytes_and_metadata(self):
        release.update(self.root, self.get)
        original_meta = dict(self.meta)
        self.meta["run_id"] = 101
        def get(url, limit):
            if url.startswith(release.PUBLIC_URL + "?"):
                return b"unexpected website response"
            return self.get(url, limit)
        with self.assertRaisesRegex(ValueError, "Website download"):
            release.update(self.root, get)
        self.assertEqual(self.public.read_bytes(), self.data)
        self.assertEqual(release.read_json(self.state / "current.json"), original_meta)
        self.assertFalse((self.state / "pending.json").exists())

    def test_network_failure_after_swap_rolls_back(self):
        def get(url, limit):
            if url.startswith(release.PUBLIC_URL + "?"):
                raise TimeoutError("offline")
            return self.get(url, limit)
        with self.assertRaises(TimeoutError):
            release.update(self.root, get)
        self.assertEqual(self.public.read_bytes(), self.old_data)
        self.assertFalse((self.state / "current.json").exists())

    def test_crash_recovery_restores_previous_before_network_call(self):
        self.state.mkdir()
        (self.state / "previous.zip").write_bytes(self.old_data)
        (self.state / "pending.json").write_bytes(release.encode({"previous": None, "candidate": self.meta}))
        self.public.write_bytes(self.data)
        def offline(url, limit):
            self.assertEqual(self.public.read_bytes(), self.old_data)
            raise TimeoutError("offline")
        with self.assertRaises(TimeoutError):
            release.update(self.root, offline)
        self.assertFalse((self.state / "pending.json").exists())

    def test_older_run_cannot_replace_successful_release(self):
        release.update(self.root, self.get)
        self.meta["run_id"] = 99
        with self.assertRaisesRegex(ValueError, "older workflow"):
            release.update(self.root, self.get)

    def test_newer_manually_published_version_is_preserved(self):
        self.meta["version"] = "0.0.9"
        self.assertIn("newer extension version", release.update(self.root, self.get))
        self.assertEqual(self.public.read_bytes(), self.old_data)
        self.assertEqual(self.calls, [release.FEED_URL])

    def test_versions_compare_numerically(self):
        self.assertGreater(release.version_parts("0.10.0"), release.version_parts("0.9.0"))
        self.assertEqual(release.version_parts("0.2"), release.version_parts("0.2.0.0"))

    def test_same_run_cannot_change_payload(self):
        release.update(self.root, self.get)
        self.meta["sha256"] = "f" * 64
        with self.assertRaisesRegex(ValueError, "without a new workflow"):
            release.update(self.root, self.get)

    def test_public_symlink_is_rejected(self):
        self.public.unlink()
        self.public.symlink_to(self.build / "background.js")
        with self.assertRaisesRegex(ValueError, "symlink"):
            release.update(self.root, self.get)

    def test_wrong_origin_and_untrusted_metadata_are_rejected(self):
        for field, value in (("backend_origin", "http://localhost:8000"),
                             ("repository", "somebody/other"), ("commit", "$(id)"),
                             ("run_id", True), ("bytes", release.MAX_ZIP + 1)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                release.validate_metadata({**self.meta, field: value})

    def test_manifest_version_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Manifest version"):
            release.inspect_zip(self.data, "9.0.0")

    def test_unsafe_archive_paths_symlinks_and_duplicates_are_rejected(self):
        for name, mode in (("../escape", stat.S_IFREG), ("/absolute", stat.S_IFREG),
                           ("a\\b", stat.S_IFREG), ("link", stat.S_IFLNK),
                           ("manifest.json", stat.S_IFREG)):
            with self.subTest(name=name):
                buffer = io.BytesIO(self.data)
                with warnings.catch_warnings(), zipfile.ZipFile(buffer, "a") as archive:
                    warnings.simplefilter("ignore", UserWarning)
                    info = zipfile.ZipInfo(name)
                    info.external_attr = (mode | 0o644) << 16
                    archive.writestr(info, "bad")
                with self.assertRaises(ValueError):
                    release.inspect_zip(buffer.getvalue())

    def test_localhost_build_is_rejected(self):
        (self.build / "background.js").write_text('const origin = "http://localhost:8000"')
        with self.assertRaisesRegex(ValueError, "Public API origin"):
            release.package(self.build, self.root / "bad", "a" * 40, 101, 1)

    def test_lock_prevents_concurrent_writer(self):
        self.state.mkdir()
        with (self.state / "update.lock").open("a") as lock:
            release.fcntl.flock(lock, release.fcntl.LOCK_EX | release.fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                release.update(self.root, self.get)

    def test_bounded_fetch_rejects_oversized_response(self):
        class Response(io.BytesIO):
            def geturl(self):
                return "https://github.com/asset"
        with patch.object(release.urllib.request, "urlopen", return_value=Response(b"12345")):
            with self.assertRaisesRegex(ValueError, "size limit"):
                release.fetch("https://github.com/asset", 4)


class PublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts/release"
        scripts.mkdir(parents=True)
        source = Path(__file__).parent
        for name in ("download_release.py", "publish.sh"):
            (scripts / name).write_bytes((source / name).read_bytes())
        build = self.root / "compiled"
        build.mkdir()
        (build / "manifest.json").write_text(json.dumps({
            "manifest_version": 3, "version": "0.2.0", "key": "stable-public-key",
            "side_panel": {"default_path": "panel.html"},
            "background": {"service_worker": "worker.js"},
        }))
        (build / "panel.html").write_text("<html></html>")
        (build / "worker.js").write_text(release.ORIGIN)
        release.package(build, self.root / "build/website-release", "a" * 40, 100, 1)
        binary = self.root / "bin"
        binary.mkdir()
        fake = binary / "gh"
        fake.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
log = Path(os.environ['GH_CALL_LOG'])
calls = log.read_text().splitlines() if log.exists() else []
args = sys.argv[1:]
with log.open('a') as f:
    f.write(json.dumps(args) + '\\n')
if args[0] == 'api':
    index = sum(json.loads(call)[0] == 'api' for call in calls)
    heads = os.environ['FAKE_HEADS'].split(',')
    print(heads[min(index, len(heads) - 1)])
elif args[:2] == ['release', 'view']:
    sys.exit(1)
''')
        fake.chmod(0o755)
        self.env = {**os.environ, "PATH": f"{binary}:{os.environ['PATH']}",
                    "GITHUB_REPOSITORY": release.REPOSITORY,
                    "GITHUB_REF": "refs/heads/frontend", "GITHUB_EVENT_NAME": "push",
                    "GITHUB_SHA": "a" * 40, "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1",
                    "GITHUB_STEP_SUMMARY": str(self.root / "summary"),
                    "GH_CALL_LOG": str(self.root / "calls"), "FAKE_HEADS": "a" * 40}

    def run_publish(self):
        result = subprocess.run(["bash", "scripts/release/publish.sh"], cwd=self.root,
                                env=self.env, capture_output=True, text=True)
        log = self.root / "calls"
        self.calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result

    def test_success_publishes_run_package_before_feed(self):
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        writes = [call for call in self.calls if call[:2] in (["release", "create"], ["release", "upload"])]
        self.assertEqual([call[2] for call in writes], ["extension-download-100-1", "extension-download-current", "extension-download-current"])
        self.assertIn("--latest=false", writes[0])

    def test_pull_requests_cannot_publish(self):
        self.env["GITHUB_EVENT_NAME"] = "pull_request"
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls, [])

    def test_stale_head_skips_all_release_writes(self):
        self.env["FAKE_HEADS"] = "b" * 40
        self.assertEqual(self.run_publish().returncode, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "api")

    def test_new_commit_during_publication_does_not_move_feed(self):
        self.env["FAKE_HEADS"] = "a" * 40 + "," + "b" * 40
        self.assertEqual(self.run_publish().returncode, 0)
        self.assertFalse(any("extension-download-current" in call for call in self.calls))

    def test_wrong_run_artifact_is_rejected_before_github_access(self):
        self.env["GITHUB_RUN_ID"] = "101"
        self.assertNotEqual(self.run_publish().returncode, 0)
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
