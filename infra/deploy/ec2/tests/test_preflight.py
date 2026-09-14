"""Configuration failures must stop deployment without disclosing secrets."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
PREFLIGHT = ROOT / "infra/deploy/ec2/preflight.py"


class PreflightTests(unittest.TestCase):
    def test_alembic_console_entry_point_imports_app_without_pythonpath(self):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["DATABASE_URL"] = "postgresql+asyncpg://threadly:test@localhost/threadly"
        result = subprocess.run(
            [str(Path(sys.executable).with_name("alembic")), "upgrade", "head", "--sql"],
            cwd=ROOT / "backend", env=env, capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("CREATE TABLE assistant_jobs", result.stdout)
        self.assertIn("e9b7120c4a63", result.stdout)

    def run_preflight(self, **overrides):
        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT / "backend"),
            "APP_ENV": "prod",
            "SECRET_KEY": "test-secret-never-print-this-value-123456789",
            "FERNET_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "DATABASE_URL": "postgresql+asyncpg://threadly:private-test-password@postgres:5432/threadly",
            "INFERENCE_PROVIDER": "bedrock",
            "BEDROCK_MODEL_ID": "",
            "GOOGLE_CLIENT_ID": "",
            "GOOGLE_CLIENT_SECRET": "",
            "GOOGLE_REDIRECT_URI": "",
            **overrides,
        }
        result = subprocess.run(
            [sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True, check=False
        )
        self.assertNotIn("test-secret-never-print", result.stdout + result.stderr)
        self.assertNotIn("private-test-password", result.stdout + result.stderr)
        return result

    def test_infrastructure_can_start_with_provider_setup_pending(self):
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PENDING: Bedrock", result.stdout)
        self.assertIn("PENDING: Google", result.stdout)

    def test_rejects_dev_auth_weak_keys_and_wrong_database(self):
        for override in [
            {"APP_ENV": "dev"},
            {"SECRET_KEY": "short"},
            {"FERNET_KEY": "invalid"},
            {"DATABASE_URL": "postgresql+asyncpg://threadly:private-test-password@other-host/db"},
            {"INFERENCE_PROVIDER": "legacy"},
        ]:
            with self.subTest(override=override):
                self.assertNotEqual(self.run_preflight(**override).returncode, 0)


if __name__ == "__main__":
    unittest.main()
