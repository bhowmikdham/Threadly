"""Start the API with proxy trust disabled unless a single private proxy is pinned."""

import ipaddress
import os

PRIVATE_PROXY_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def uvicorn_command(trusted_proxy_ip: str | None = None) -> list[str]:
    command = ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
    if not trusted_proxy_ip:
        return [*command, "--no-proxy-headers"]
    try:
        address = ipaddress.ip_address(trusted_proxy_ip)
    except ValueError as exc:
        raise ValueError("THREADLY_TRUSTED_PROXY_IP must be one private IP address") from exc
    if not isinstance(address, ipaddress.IPv4Address) or not any(
        address in network for network in PRIVATE_PROXY_NETWORKS
    ):
        raise ValueError("THREADLY_TRUSTED_PROXY_IP must be one private IP address")
    return [*command, "--proxy-headers", "--forwarded-allow-ips", str(address)]


if __name__ == "__main__":
    os.execvp("uvicorn", uvicorn_command(os.environ.get("THREADLY_TRUSTED_PROXY_IP")))
