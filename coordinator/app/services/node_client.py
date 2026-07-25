"""Outbound HTTP to the hospital nodes.

The coordinator never holds hospital data itself — it calls each node. This
module centralizes that outbound plumbing (fan-out, timeouts) so route handlers
stay clean. Extend with query/auth/retrieve calls as those contracts land.
"""
from concurrent.futures import ThreadPoolExecutor

import requests

from config import Config


def ping_nodes(nodes: dict[str, str], timeout: float = Config.NODE_TIMEOUT) -> list[dict]:
    """Hit each node's /health concurrently; return reachability per node."""

    def _ping(item: tuple[str, str]) -> dict:
        node_id, base_url = item
        try:
            r = requests.get(f"{base_url}/health", timeout=timeout)
            r.raise_for_status()
            return {"node": node_id, "url": base_url, "reachable": True, "detail": r.json()}
        except requests.RequestException as exc:
            return {"node": node_id, "url": base_url, "reachable": False, "detail": str(exc)}

    with ThreadPoolExecutor(max_workers=max(1, len(nodes))) as pool:
        return list(pool.map(_ping, nodes.items()))
