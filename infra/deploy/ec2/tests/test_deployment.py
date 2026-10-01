"""Execute the deployment shell with synthetic tools; never contact a host or Docker."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = 'a' * 40
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name, args = Path(sys.argv[0]).name, sys.argv[1:]
with open(os.environ['DEPLOY_CALLS'], 'a') as f:
    f.write(json.dumps([name, *args]) + '\n')
if name == 'git':
    if args[0] == 'clone':
        release = Path(args[-1])
        release.mkdir(parents=True)
        for page in ['index.html', 'install/index.html', 'privacy/index.html', 'terms/index.html']:
            target = release / 'website' / page
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('__LAUNCH_CONTACT__' if os.environ.get('UNFINISHED_SITE') else 'Reviewed synthetic page')
    if 'get-url' in args: print('https://github.com/bhowmikdham/Threadly.git')
    if 'rev-parse' in args: print('a' * 40)
elif name == 'stat': print('0:600')
elif name == 'docker':
    if args[:3] == ['network', 'ls', '-q']: print('synthetic-network')
    if args[:2] == ['network', 'inspect']:
        subnet = '172.30.247.0/24' if os.environ.get('FAIL_NETWORK_OVERLAP') else '172.18.0.0/16'
        print(json.dumps([{'Name': 'another-network', 'IPAM': {'Config': [{'Subnet': subnet}]}}]))
    if args[:1] == ['inspect']: print('true')
    if 'ps' in args and '-q' in args: print('synthetic-container')
    if 'images' in args: print('[]')
    if 'exec' in args: print('-- synthetic backup')
    if 'alembic' in args and 'upgrade' in args and os.environ.get('FAIL_MIGRATION'): sys.exit(7)
    if 'up' in args and args[-1] == 'caddy' and os.environ.get('FAIL_CADDY'): sys.exit(8)
elif name == 'curl':
    if any(arg.startswith('https://') for arg in args) and (os.environ.get('FAIL_PUBLIC_TLS') or (os.environ.get('FAIL_SITE_TLS') and any(arg == 'https://threadly.au/' for arg in args))):
        sys.exit(60)
'''


class DeploymentTests(unittest.TestCase):
    def run_deploy(self, fail=False, *, public=False, domain='', fail_caddy=False,
                   fail_public_tls=False, fail_network_overlap=False, extra_args=(), launch=False,
                   missing_bundle=False, unfinished_site=False, fail_site_tls=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            host = root / 'host'
            (host / 'secrets').mkdir(parents=True)
            (host / 'secrets/threadly.env').write_text(f'SYNTHETIC=true\nDOMAIN={domain}\n')
            (host / 'BOOTSTRAP_READY').touch()
            if not missing_bundle:
                (host / 'public-downloads').mkdir()
                (host / 'public-downloads/threadly-extension.zip').write_bytes(b'synthetic fixture')
            bins = root / 'bin'
            bins.mkdir()
            for name in ['git', 'docker', 'stat', 'mountpoint', 'flock', 'curl', 'systemctl']:
                p = bins / name
                p.write_text(FAKE)
                p.chmod(0o755)
            source = (ROOT / 'deploy-app.sh').read_text()
            source = source.replace('[[ $EUID -eq 0 ]]', 'true')
            source = source.replace('/opt/threadly', str(host)).replace('/srv/threadly-data', str(host))
            source = source.replace('/var/lock/threadly-deploy.lock', str(root / 'lock'))
            source = source.replace(
                '$RELEASE_DIR/infra/deploy/ec2/public-network-preflight.py',
                str(ROOT / 'public-network-preflight.py'),
            )
            script = root / 'deploy.sh'
            script.write_text(source)
            log = root / 'calls.jsonl'
            args = ['bash', str(script), RELEASE]
            if launch:
                args.append('--public-launch')
            elif public:
                args.append('--public-https')
            args.extend(extra_args)
            result = subprocess.run(args, capture_output=True, text=True, check=False,
                env={**os.environ, 'PATH': str(bins) + os.pathsep + os.environ['PATH'],
                     'DEPLOY_CALLS': str(log),
                     **({'FAIL_MIGRATION': '1'} if fail else {}),
                     **({'UNFINISHED_SITE': '1'} if unfinished_site else {}),
                     **({'FAIL_SITE_TLS': '1'} if fail_site_tls else {}),
                     **({'FAIL_CADDY': '1'} if fail_caddy else {}),
                     **({'FAIL_PUBLIC_TLS': '1'} if fail_public_tls else {}),
                     **({'FAIL_NETWORK_OVERLAP': '1'} if fail_network_overlap else {})})
            calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
            mode = host / 'deployment/current-mode'
            return result, calls, mode.read_text().strip() if mode.exists() else None

    def test_stops_all_workers_before_migration_and_starts_matching_release(self):
        result, calls, mode = self.run_deploy(domain='api.example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        stop = next(i for i, c in enumerate(calls) if c[:2] == ['docker', 'compose']
                    and 'stop' in c and 'assistant-worker' in c)
        migrate = next(i for i, c in enumerate(calls) if 'alembic' in c and 'upgrade' in c)
        start = next(i for i, c in enumerate(calls) if 'up' in c and 'action-worker' in c)
        self.assertLess(stop, migrate)
        self.assertLess(migrate, start)
        self.assertIn('assistant-worker', calls[stop])
        self.assertIn('action-worker', calls[stop])
        self.assertIn('sync-worker', calls[stop])
        self.assertNotIn('sync-worker', calls[start])
        self.assertFalse(any('ps' in c and '-q' in c and 'sync-worker' in c for c in calls))
        self.assertTrue(any('ps' in c and '-q' in c and 'action-worker' in c for c in calls))
        self.assertIn('DEPLOYMENT_READY commit=' + RELEASE, result.stdout)
        self.assertEqual(mode, 'private')
        self.assertFalse(any(c[0] == 'systemctl' for c in calls))
        self.assertIn('PRIVATE_TUNNEL_READY', result.stdout)
        self.assertFalse(any('up' in c and c[-1] == 'caddy' for c in calls))
        self.assertFalse(any(c[0] == 'curl' and any(arg.startswith('https://') for arg in c)
                             for c in calls))
        # A private redeploy explicitly disables any previously running Caddy.
        self.assertTrue(any('stop' in c and c[-1] == 'caddy' for c in calls))

    def test_failed_migration_does_not_restart_workers_or_claim_ready(self):
        result, calls, mode = self.run_deploy(fail=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any('up' in c and 'action-worker' in c for c in calls))
        self.assertNotIn('DEPLOYMENT_READY', result.stdout)
        self.assertIsNone(mode)

    def test_public_mode_requires_one_valid_domain_before_build_or_start(self):
        for domain in ['', 'localhost', '127.0.0.1', 'https://api.example.test',
                       'bad..example.test', 'api.example.test.', 'api.example.test:443']:
            with self.subTest(domain=domain):
                result, calls, mode = self.run_deploy(public=True, domain=domain)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('DOMAIN', result.stderr)
                self.assertFalse(any('up' in c or 'build' in c for c in calls))
                self.assertIsNone(mode)

    def test_public_mode_starts_caddy_only_after_migration_and_verifies_tls(self):
        result, calls, mode = self.run_deploy(public=True, domain='api.example.test')
        self.assertEqual(result.returncode, 0, result.stderr)
        stop_caddy = next(i for i, c in enumerate(calls) if 'stop' in c and c[-1] == 'caddy')
        migrate = next(i for i, c in enumerate(calls) if 'alembic' in c and 'upgrade' in c)
        start_api = next(i for i, c in enumerate(calls) if 'up' in c and 'action-worker' in c)
        start_caddy = next(i for i, c in enumerate(calls) if 'up' in c and c[-1] == 'caddy')
        tls = next(i for i, c in enumerate(calls) if c[0] == 'curl' and
                   'https://api.example.test/healthz' in c)
        self.assertLess(stop_caddy, migrate)
        self.assertLess(migrate, start_api)
        self.assertLess(start_api, start_caddy)
        self.assertLess(start_caddy, tls)
        self.assertTrue(any(arg.endswith('/compose.public-https.yml') for arg in calls[start_caddy]))
        self.assertIn('--resolve', calls[tls])
        self.assertIn('--noproxy', calls[tls])
        self.assertIn('api.example.test:443:127.0.0.1', calls[tls])
        self.assertEqual(mode, 'public-https')
        self.assertIn('LOCAL_HTTPS_READY https://api.example.test', result.stdout)

    def test_public_network_collision_stops_before_any_application_change(self):
        result, calls, mode = self.run_deploy(
            public=True, domain='api.example.test', fail_network_overlap=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('overlaps the public ingress subnet', result.stderr)
        self.assertIsNone(mode)
        self.assertFalse(any('up' in call or 'stop' in call or 'alembic' in call for call in calls))

    def test_public_start_or_tls_failure_cannot_claim_readiness(self):
        for failure in ({'fail_caddy': True}, {'fail_public_tls': True}):
            with self.subTest(failure=failure):
                result, calls, mode = self.run_deploy(
                    public=True, domain='api.example.test', **failure
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('DEPLOYMENT_READY', result.stdout)
                self.assertIsNone(mode)
                if failure.get('fail_public_tls'):
                    start = next(i for i, c in enumerate(calls) if 'up' in c and c[-1] == 'caddy')
                    self.assertTrue(any(i > start and 'stop' in c and c[-1] == 'caddy'
                                        for i, c in enumerate(calls)))

    def test_launch_checks_every_hostname_and_uses_website_override(self):
        result, calls, mode = self.run_deploy(launch=True, domain='api.threadly.au')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(mode, 'public-launch')
        self.assertIn(['systemctl', 'disable', '--now', 'threadly-autostop.timer'], calls)
        start = next(c for c in calls if 'up' in c and c[-1] == 'caddy')
        self.assertTrue(any(arg.endswith('/compose.public-launch.yml') for arg in start))
        for url in ('https://api.threadly.au/healthz', 'https://threadly.au/', 'https://www.threadly.au/'):
            self.assertTrue(any(c[0] == 'curl' and url in c for c in calls), url)

    def test_unfinished_launch_cannot_stop_existing_application(self):
        for options in ({'missing_bundle': True}, {'unfinished_site': True},
                        {'domain': 'another.example.test'}):
            with self.subTest(options=options):
                result, calls, mode = self.run_deploy(
                    launch=True, **{'domain': 'api.threadly.au', **options}
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(mode)
                self.assertFalse(any('stop' in c or 'build' in c or 'up' in c for c in calls))

    def test_site_certificate_failure_stops_public_ingress(self):
        result, calls, mode = self.run_deploy(
            launch=True, domain='api.threadly.au', fail_site_tls=True
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(mode)
        self.assertIn('Public site TLS failed', result.stderr)
        self.assertFalse(any(c[0] == 'systemctl' for c in calls))
        start = next(i for i, c in enumerate(calls) if 'up' in c and c[-1] == 'caddy')
        self.assertTrue(any(i > start and 'stop' in c and c[-1] == 'caddy'
                            for i, c in enumerate(calls)))

    def test_unknown_mode_does_not_start_anything(self):
        result, calls, mode = self.run_deploy(extra_args=('--public',))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
        self.assertIsNone(mode)

    @unittest.skipUnless(shutil.which('docker'), 'Docker Compose is unavailable')
    def test_compose_profile_keeps_only_caddy_public(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / 'threadly.env'
            env_file.write_text('SYNTHETIC=true\n')
            env = {**os.environ, 'THREADLY_RELEASE': RELEASE,
                   'THREADLY_ENV_FILE': str(env_file), 'POSTGRES_USER': 'threadly',
                   'POSTGRES_PASSWORD': 'synthetic', 'POSTGRES_DB': 'threadly',
                   'DOMAIN': 'api.example.test', 'THREADLY_TRUSTED_PROXY_IP': '172.30.247.2'}
            base = ['docker', 'compose', '-f', str(ROOT / 'compose.staging.yml')]

            def config(*options):
                result = subprocess.run([*base, *options, 'config', '--format', 'json'],
                                        capture_output=True, text=True, env=env, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)

            private = config()
            public = config('-f', str(ROOT / 'compose.public-https.yml'))
            self.assertEqual(private['name'], public['name'])
            self.assertNotIn('caddy', private['services'])
            self.assertNotIn('ingress_net', private['networks'])
            self.assertEqual(set(private['services']['api']['networks']), {'threadly_net'})
            self.assertEqual(private['services']['api']['environment']['THREADLY_TRUSTED_PROXY_IP'], '')
            self.assertIn('caddy', public['services'])
            self.assertEqual(public['services']['api']['environment']['THREADLY_TRUSTED_PROXY_IP'],
                             '172.30.247.2')
            self.assertEqual(public['services']['caddy']['image'], 'caddy:2.11.4')
            self.assertEqual(public['services']['api']['ports'][0]['host_ip'], '127.0.0.1')
            self.assertEqual(public['services']['caddy']['ports'][0]['published'], '443')
            self.assertEqual(public['services']['caddy']['networks']['ingress_net']
                             ['ipv4_address'], '172.30.247.2')
            self.assertEqual(set(public['services']['caddy']['networks']), {'ingress_net'})
            self.assertEqual(set(public['services']['api']['networks']),
                             {'ingress_net', 'threadly_net'})
            for service in ('postgres', 'chroma', 'assistant-worker', 'action-worker'):
                self.assertNotIn('ports', public['services'][service])
            self.assertEqual(private['volumes']['pgdata'], public['volumes']['pgdata'])
            self.assertIn('max_size 10MB', (ROOT.parent.parent / 'caddy/Caddyfile').read_text())


if __name__ == '__main__':
    unittest.main()
