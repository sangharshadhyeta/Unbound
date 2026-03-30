"""
Kademlia DHT — peer and capability discovery.

Miners announce their capabilities into the DHT so submitters and other
nodes can find them without a central directory.

Key schema:
  "cap:<capability>"  →  JSON list of {node_id, url} entries
  "node:<node_id>"    →  JSON {node_id, url, capabilities}

Bootstrap nodes are the only fixed entry points. After joining, all
discovery is fully P2P.
"""

import asyncio
import json
import logging
from typing import List, Optional

from kademlia.network import Server

logger = logging.getLogger(__name__)

# Default DHT port. Runs alongside the WebSocket server.
DEFAULT_DHT_PORT = 4433


class DHT:
    def __init__(self, node_id: str, port: int = DEFAULT_DHT_PORT):
        self._node_id = node_id
        self._port    = port
        self._server  = Server()

    async def start(self, bootstrap_nodes: Optional[List[tuple]] = None):
        """
        Start listening and optionally bootstrap from known peers.

        bootstrap_nodes: list of (host, port) tuples for seed nodes.
        If None or empty, starts an isolated DHT (useful for private clusters).
        """
        await self._server.listen(self._port)
        if bootstrap_nodes:
            await self._server.bootstrap(bootstrap_nodes)
            logger.info(f"DHT joined via {len(bootstrap_nodes)} bootstrap node(s)")
        else:
            logger.info(f"DHT started in isolated mode on port {self._port}")

    async def announce(self, capabilities: List[str], ws_url: str):
        """Publish this node's capabilities and endpoint into the DHT."""
        entry = json.dumps({"node_id": self._node_id, "url": ws_url})

        # Announce under each capability key
        for cap in capabilities:
            key = f"cap:{cap}"
            existing_raw = await self._server.get(key)
            existing = json.loads(existing_raw) if existing_raw else []
            # Replace stale entry for this node, then append fresh one
            existing = [e for e in existing if e.get("node_id") != self._node_id]
            existing.append({"node_id": self._node_id, "url": ws_url})
            await self._server.set(key, json.dumps(existing))

        # Also announce under node ID for direct lookup
        await self._server.set(
            f"node:{self._node_id}",
            json.dumps({
                "node_id":      self._node_id,
                "url":          ws_url,
                "capabilities": capabilities,
            }),
        )
        logger.info(f"DHT announced {self._node_id} caps={capabilities} url={ws_url}")

    async def find_miners(self, capability: str) -> List[dict]:
        """Return list of {node_id, url} for miners with the given capability."""
        raw = await self._server.get(f"cap:{capability}")
        return json.loads(raw) if raw else []

    async def find_node(self, node_id: str) -> Optional[dict]:
        """Look up a specific node by ID. Returns {node_id, url, capabilities} or None."""
        raw = await self._server.get(f"node:{node_id}")
        return json.loads(raw) if raw else None

    def stop(self):
        self._server.stop()
        logger.info("DHT stopped")
