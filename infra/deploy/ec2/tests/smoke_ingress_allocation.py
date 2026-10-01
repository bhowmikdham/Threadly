"""Start API then proxy in a disposable Docker network using real Compose addresses."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

with tempfile.TemporaryDirectory() as folder:
    env_file = Path(folder) / 'synthetic.env'
    env_file.write_text('')
    env = {**os.environ, 'THREADLY_RELEASE': 'network-smoke',
           'THREADLY_ENV_FILE': str(env_file), 'POSTGRES_USER': 'synthetic',
           'POSTGRES_PASSWORD': 'synthetic', 'POSTGRES_DB': 'synthetic',
           'DOMAIN': 'example.test', 'THREADLY_TRUSTED_PROXY_IP': '172.30.247.2'}
    config = json.loads(subprocess.check_output([
        'docker', 'compose', '-f', str(ROOT / 'compose.staging.yml'),
        '-f', str(ROOT / 'compose.public-https.yml'), 'config', '--format', 'json'
    ], env=env, text=True))
    subnet = config['networks']['ingress_net']['ipam']['config'][0]['subnet']
    proxy_ip = config['services']['caddy']['networks']['ingress_net']['ipv4_address']
    image = config['services']['caddy']['image']
    name = 'threadly-ingress-smoke-' + uuid.uuid4().hex[:10]
    containers = []
    created = False
    try:
        docker('image', 'inspect', image)
        docker('network', 'create', '--subnet', subnet, name)
        created = True
        for service in ['api', 'caddy']:
            address = config['services'][service]['networks']['ingress_net'].get('ipv4_address')
            args = ['run', '-d', '--name', name + '-' + service, '--network', name]
            if address:
                args += ['--ip', address]
            # Use only the image's sleep utility: no public ports, credentials or app data.
            args += ['--entrypoint', 'sleep', image, '120']
            containers.append(name + '-' + service)
            docker(*args)
        endpoints = json.loads(docker('network', 'inspect', name))[0]['Containers']
        addresses = {entry['Name']: entry['IPv4Address'].split('/')[0] for entry in endpoints.values()}
        assert addresses[name + '-caddy'] == proxy_ip
        assert addresses[name + '-api'] != proxy_ip
        print('PASS: API starts before proxy without taking its trusted address:', addresses)
    finally:
        for container in reversed(containers):
            subprocess.run(['docker', 'rm', '-f', container], check=False, capture_output=True)
        if created:
            docker('network', 'rm', name)
