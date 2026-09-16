"""Execute the deployment shell with synthetic tools; never contact a host or Docker."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = 'a' * 40
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ['DEPLOY_CALLS'], 'a') as f:
    f.write(json.dumps([name, *args]) + '\n')
if name == 'git':
    if args[0] == 'clone': Path(args[-1]).mkdir(parents=True)
    if 'get-url' in args: print('https://github.com/bhowmikdham/Threadly.git')
    if 'rev-parse' in args: print('a' * 40)
elif name == 'stat': print('0:600')
elif name == 'docker':
    if args[:1] == ['inspect']: print('true')
    if 'ps' in args and '-q' in args: print('synthetic-container')
    if 'images' in args: print('[]')
    if 'exec' in args: print('-- synthetic backup')
    if 'alembic' in args and 'upgrade' in args and os.environ.get('FAIL_MIGRATION'): sys.exit(7)
'''


class DeploymentTests(unittest.TestCase):
    def run_deploy(self, fail=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / 'host'
            (host / 'secrets').mkdir(parents=True)
            (host / 'secrets/threadly.env').write_text('SYNTHETIC=true\n')
            (host / 'BOOTSTRAP_READY').touch()
            bins = root / 'bin'
            bins.mkdir()
            for name in ['git', 'docker', 'stat', 'mountpoint', 'flock', 'curl']:
                p = bins / name
                p.write_text(FAKE)
                p.chmod(0o755)
            source = (ROOT / 'deploy-app.sh').read_text()
            source = source.replace('[[ $EUID -eq 0 ]]', 'true')
            source = source.replace('/opt/threadly', str(host)).replace('/srv/threadly-data', str(host))
            source = source.replace('/var/lock/threadly-deploy.lock', str(root / 'lock'))
            script = root / 'deploy.sh'
            script.write_text(source)
            log = root / 'calls.jsonl'
            result = subprocess.run(['bash', str(script), RELEASE], capture_output=True, text=True,
                env={**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'],
                     'DEPLOY_CALLS': str(log), **({'FAIL_MIGRATION': '1'} if fail else {})})
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            return result, calls

    def test_stops_both_workers_before_migration_and_starts_matching_release(self):
        result, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'compose'] and 'stop' in c)
        migrate = next(i for i, c in enumerate(calls) if 'alembic' in c and 'upgrade' in c)
        start = next(i for i, c in enumerate(calls) if 'up' in c and 'action-worker' in c)
        self.assertLess(stop, migrate)
        self.assertLess(migrate, start)
        self.assertIn('assistant-worker', calls[stop])
        self.assertIn('action-worker', calls[stop])
        self.assertTrue(any('ps' in c and '-q' in c and 'action-worker' in c for c in calls))
        self.assertIn('DEPLOYMENT_READY commit=' + RELEASE, result.stdout)

    def test_failed_migration_does_not_restart_workers_or_claim_ready(self):
        result, calls = self.run_deploy(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in c and 'action-worker' in c for c in calls))
        self.assertNotIn('DEPLOYMENT_READY', result.stdout)


if __name__ == '__main__':
    unittest.main()
