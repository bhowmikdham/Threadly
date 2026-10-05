"""Verify the real Caddy download routes on an isolated local container."""
import json
import http.client
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True).strip()


def main():
    config = (ROOT / "infra/caddy/Caddyfile.threadly-au").read_text()
    # Keep the actual public site's handlers; substitute only host/listener and
    # the unrelated API import so this test never contacts a production domain.
    config = config.replace("import /etc/caddy/api.Caddyfile", "")
    config = config.split("\nwww.threadly.au {")[0].replace("threadly.au {", ":8080 {")
    config = "{\n    admin off\n    auto_https off\n}\n" + config
    with tempfile.TemporaryDirectory() as temporary:
        fixtures = Path(temporary)
        (fixtures / "Caddyfile").write_text(config)
        downloads = fixtures / "downloads"
        downloads.mkdir()
        payload = b"synthetic public download"
        (downloads / "threadly-extension.zip").write_bytes(payload)
        (downloads / "threadly-extension.json").write_text('{"version":"0.2.3"}')
        (downloads / "not-public.txt").write_text("must not be served")
        container = docker(
            "run", "--rm", "-d", "-p", "127.0.0.1::8080",
            "-v", f"{fixtures / 'Caddyfile'}:/etc/caddy/Caddyfile:ro",
            "-v", f"{downloads}:/srv/downloads:ro",
            "-v", f"{ROOT / 'website'}:/srv/site:ro", "caddy:2.11.4",
        )
        try:
            details = json.loads(docker("inspect", container))[0]
            port = details["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"]
            origin = f"http://127.0.0.1:{port}"
            for attempt in range(50):
                try:
                    urllib.request.urlopen(origin, timeout=1).close()
                    break
                except (urllib.error.URLError, OSError, http.client.HTTPException):
                    if attempt == 49:
                        raise
                    time.sleep(0.1)
            checks = 0
            for suffix in ["", "?sha256=" + "a" * 64, "?download=1"]:
                with urllib.request.urlopen(origin + "/downloads/threadly-extension.zip" + suffix) as response:
                    assert response.headers["Cache-Control"] == "no-store"
                    assert response.headers["Content-Disposition"] == "attachment; filename=threadly-extension.zip"
                    assert response.read() == payload
                    checks += 1
            with urllib.request.urlopen(origin + "/downloads/threadly-extension.json") as response:
                assert response.headers["Cache-Control"] == "no-store"
                checks += 1
            for path in ["/", "/install/", "/extension-version.js?v=2"]:
                with urllib.request.urlopen(origin + path) as response:
                    assert response.headers["Cache-Control"] == "no-cache"
                    assert response.headers["X-Content-Type-Options"] == "nosniff"
                    checks += 1
            try:
                urllib.request.urlopen(origin + "/downloads/not-public.txt")
                raise AssertionError("Unexpected public download path")
            except urllib.error.HTTPError as error:
                assert error.code == 404
                checks += 1
            print(f"{checks} real Caddy route/header checks passed")
        finally:
            docker("rm", "-f", container)


if __name__ == "__main__":
    main()
