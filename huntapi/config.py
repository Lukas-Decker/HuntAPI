"""Hunt's public endpoint discovery.

The client bootstraps from a hardcoded URL in ``GameHunt.dll``:

    https://config.huntshowdown.com/v1/{ENV}/endpoints.json

``ENV`` comes from the ``+online_account_client_settings`` command line argument, which
the retail launcher sets to ``live``. This file is unauthenticated and public - fetching
it is exactly what the game does before it has any credentials, and matches the
``WaitingForEnvEndpoints`` state in Game.log.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

CONFIG_URL = "https://config.huntshowdown.com/v1/{env}/endpoints.json"
DEFAULT_ENV = "live"
USER_AGENT = "HuntAPI/0.3 (+local tooling)"


@dataclass(frozen=True)
class Endpoint:
    address: str
    port: int
    region: str
    platforms: tuple[str, ...]
    ping_port: int = 0
    cert_url: str = ""

    @property
    def is_pc(self) -> bool:
        return "PC" in self.platforms

    def __str__(self) -> str:
        return f"{self.address}:{self.port} [{self.region}] {'/'.join(self.platforms)}"


def fetch(env: str = DEFAULT_ENV, timeout: float = 10.0) -> list[Endpoint]:
    request = urllib.request.Request(
        CONFIG_URL.format(env=env), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [
        Endpoint(
            address=entry["address"],
            port=int(entry["port"]),
            region=entry.get("region", "?"),
            platforms=tuple(entry.get("platforms", ())),
            ping_port=int(entry.get("pingPort", 0)),
            cert_url=entry.get("certFetchingUrl", ""),
        )
        for entry in payload
    ]


def _to_endpoint(entry: dict) -> Endpoint:
    return Endpoint(
        address=entry["address"],
        port=int(entry["port"]),
        region=entry.get("region", "?"),
        platforms=tuple(entry.get("platforms", ())),
        ping_port=int(entry.get("ping_port", 0)),
        cert_url=entry.get("cert_url", ""),
    )


def _to_dict(endpoint: Endpoint) -> dict:
    return {
        "address": endpoint.address,
        "port": endpoint.port,
        "region": endpoint.region,
        "platforms": list(endpoint.platforms),
        "ping_port": endpoint.ping_port,
        "cert_url": endpoint.cert_url,
    }


def load_cached(cache: str | Path, env: str = DEFAULT_ENV, max_age_s: float = 3600.0) -> list[Endpoint]:
    """Fetch, but reuse a recent on-disk copy so repeated commands do not re-request."""
    cache = Path(cache)
    if cache.is_file() and (time.time() - cache.stat().st_mtime) < max_age_s:
        return [_to_endpoint(e) for e in json.loads(cache.read_text(encoding="utf-8"))]

    endpoints = fetch(env)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps([_to_dict(e) for e in endpoints], indent=2), encoding="utf-8"
    )
    return endpoints


def tcp_latency(endpoint: Endpoint, timeout: float = 3.0) -> float | None:
    """Time a TCP handshake to the front-end. Returns milliseconds, or None if refused."""
    start = time.perf_counter()
    try:
        with socket.create_connection((endpoint.address, endpoint.port), timeout=timeout):
            return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None


def regions(endpoints: list[Endpoint], pc_only: bool = True) -> dict[str, list[Endpoint]]:
    grouped: dict[str, list[Endpoint]] = {}
    for endpoint in endpoints:
        if pc_only and not endpoint.is_pc:
            continue
        grouped.setdefault(endpoint.region, []).append(endpoint)
    return dict(sorted(grouped.items()))
