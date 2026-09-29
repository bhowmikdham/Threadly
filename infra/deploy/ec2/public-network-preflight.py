"""Refuse a public deployment if its pinned proxy subnet conflicts on the host."""

import ipaddress
import json
import subprocess
import sys

INGRESS = ipaddress.ip_network("172.30.247.0/29")
NETWORK_NAME = "threadly_ingress_net"


def check_networks(networks: list[dict]) -> None:
    for network in networks:
        name = network.get("Name", "unknown")
        labels = network.get("Labels") or {}
        subnets = [
            ipaddress.ip_network(entry["Subnet"], strict=False)
            for entry in (network.get("IPAM") or {}).get("Config") or []
            if entry.get("Subnet")
        ]
        if name == NETWORK_NAME:
            if (
                labels.get("com.docker.compose.project") != "threadly"
                or labels.get("com.docker.compose.network") != "ingress_net"
                or INGRESS not in subnets
            ):
                raise ValueError("Existing Threadly ingress network does not match the public release")
            continue
        if any(subnet.version == 4 and subnet.overlaps(INGRESS) for subnet in subnets):
            raise ValueError(f"Docker network {name} overlaps the public ingress subnet")


def main() -> None:
    ids = subprocess.run(
        ["docker", "network", "ls", "-q"], check=True, text=True, capture_output=True
    ).stdout.split()
    if ids:
        networks = json.loads(
            subprocess.run(
                ["docker", "network", "inspect", *ids],
                check=True,
                text=True,
                capture_output=True,
            ).stdout
        )
        check_networks(networks)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"Public ingress network preflight failed: {exc}", file=sys.stderr)
        sys.exit(1)
