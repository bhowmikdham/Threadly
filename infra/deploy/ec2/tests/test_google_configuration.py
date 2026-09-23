"""Offline tests; no Google credentials, root access or provider calls required."""

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "google_setup", Path(__file__).parents[1] / "configure-google.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
CLIENT = "123-synthetic.apps.googleusercontent.com"
SECRET = "GOCSPX-synthetic-secret"


class ConfigurationTests(unittest.TestCase):
    def test_preserves_other_settings_and_protects_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "threadly.env"
            original = (
                "SECRET_KEY=untouched\nBEDROCK_MODEL_ID=unchanged\n"
                "GOOGLE_CLIENT_SECRET=old\nexport GOOGLE_CLIENT_ID=old\n"
                "EMAIL_WRITES_ENABLED=true\nWRITE_PILOT_USER_IDS=1\n"
            )
            path.write_text(original)
            path.chmod(0o600)
            backup = Path(setup.update_env(path, CLIENT, SECRET))
            self.assertEqual(backup.read_text(), original)
            for candidate in (path, backup):
                self.assertEqual(stat.S_IMODE(candidate.stat().st_mode), 0o600)
            result = path.read_text()
            self.assertIn("SECRET_KEY=untouched\nBEDROCK_MODEL_ID=unchanged", result)
            self.assertEqual(result.count("GOOGLE_CLIENT_SECRET="), 1)
            self.assertIn("GOOGLE_CLIENT_SECRET=" + SECRET, result)
            self.assertIn("GOOGLE_ALLOW_LOOPBACK_TEST_CALLBACK=true", result)
            self.assertIn("EMAIL_WRITES_ENABLED=false", result)
            self.assertIn("CALENDAR_WRITES_ENABLED=false", result)
            self.assertIn("WRITE_PILOT_USER_IDS=\n", result)

    def test_bad_input_and_unsafe_permissions_do_not_change_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "threadly.env"
            path.write_text("unchanged")
            path.chmod(0o600)
            for client, secret in [(CLIENT, "secret\nINJECTED=true"), ("bad", SECRET)]:
                with self.assertRaises(ValueError):
                    setup.update_env(path, client, secret)
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                setup.update_env(path, CLIENT, SECRET)
            self.assertEqual(path.read_text(), "unchanged")

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target"
            target.write_text("unchanged")
            target.chmod(0o600)
            path = Path(directory) / "threadly.env"
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                setup.update_env(path, CLIENT, SECRET)
            self.assertEqual(target.read_text(), "unchanged")
