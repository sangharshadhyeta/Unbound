"""
Unbound CLI

Commands:
  unbound node      -- start a full node (API + WebSocket server)
  unbound mine      -- start a miner daemon
  unbound submit    -- compile and submit a job
  unbound result    -- poll for and retrieve job results
  unbound balance   -- check UBD balance
  unbound faucet    -- credit test UBD to an address (dev only)
  unbound batch     -- offline batch: export / run / import
  unbound cluster   -- private compute cluster (no payment)
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import click
import requests


API_URL = "http://localhost:8000"
WS_URL  = "ws://localhost:8765"


@click.group()
def cli():
    """Unbound — distributed trustless compute."""


# ── Node ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--api-port",       default=8000,        show_default=True)
@click.option("--ws-port",        default=8765,        show_default=True)
@click.option("--db",             default="unbound.db", show_default=True)
@click.option("--slash-fraction", default=0.25,        show_default=True,
              help="Fraction of chunk reward burned on invalid result (0–1).")
@click.option("--identity",       default=None,        metavar="PATH",
              help="Path to Ed25519 identity key (auto-generated if absent).")
@click.option("--peer",           "peers", multiple=True, metavar="WS_URL",
              help="Peer coordinator URL for gossip (repeatable).")
@click.option("--dht-bootstrap",  "dht_bootstrap", multiple=True, metavar="HOST:PORT",
              help="DHT bootstrap node (repeatable). Omit for isolated mode.")
@click.option("--tls-cert",       default=None, metavar="PATH",
              help="TLS certificate file — enables wss:// on --ws-port.")
@click.option("--tls-key",        default=None, metavar="PATH",
              help="TLS private key file (required with --tls-cert).")
def node(api_port, ws_port, db, slash_fraction, identity,
         peers, dht_bootstrap, tls_cert, tls_key):
    """Start a full Unbound node (API + WebSocket server).

    Enable wss:// on port 443 with --ws-port 443 --tls-cert cert.pem --tls-key key.pem
    Traffic on port 443 TLS is indistinguishable from HTTPS to any inspector.

    Connect to peer coordinators with --peer ws://other-node:8765 (repeatable).
    Jobs submitted to any peer propagate to all peers via gossip.
    """
    import uvicorn
    import threading

    from ..ledger.ledger import Ledger
    from ..chain.chain import Chain
    from ..registry.registry import Registry
    from ..network.server import NodeServer
    from ..api.app import app, init as api_init

    ledger   = Ledger(db)
    registry = Registry()
    chain    = Chain(ledger)

    dht_nodes = [tuple(h.rsplit(":", 1)) for h in dht_bootstrap] if dht_bootstrap else None
    if dht_nodes:
        dht_nodes = [(h, int(p)) for h, p in dht_nodes]

    server = NodeServer(
        registry, chain, ledger,
        ws_port=ws_port,
        slash_fraction=slash_fraction,
        identity_path=Path(identity) if identity else None,
        peers=list(peers) or None,
        dht_bootstrap=dht_nodes,
        tls_cert=tls_cert,
        tls_key=tls_key,
    )

    api_init(registry, ledger)

    scheme = "wss" if tls_cert else "ws"
    click.echo(
        f"Node {server.node_id[:12]}…  "
        f"API:{api_port}  {scheme}:{ws_port}  DB:{db}  "
        f"slash:{int(slash_fraction * 100)}%"
        + (f"  peers:{len(peers)}" if peers else "")
        + (f"  dht:on" if dht_bootstrap else "")
    )
    if not tls_cert:
        click.echo(
            "  Tip: use --tls-cert / --tls-key --ws-port 443 for wss:// "
            "(traffic indistinguishable from HTTPS)"
        )

    def run_ws():
        asyncio.run(server.start())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")


# ── Mine ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--server",      "servers",      multiple=True, default=[WS_URL],
              show_default=True, metavar="WS_URL",
              help="Coordinator URL (repeatable for automatic failover).")
@click.option("--identity",    default=None,   metavar="PATH",
              help="Path to Ed25519 identity key (auto-generated if absent).")
@click.option("--capability",  "capabilities", multiple=True,
              help="Declare a capability tag (repeatable). e.g. --capability gpu")
@click.option("--volunteer",   is_flag=True,   default=False,
              help="Contribute freely — no UBD earned.")
@click.option("--stake",       default=0,      show_default=True,
              help="UBD to lock as stake. Unlocks jobs that require staked miners.")
@click.option("--cached-cid",  "cached_cids",  multiple=True,
              help="IPFS CID of a locally cached dataset (repeatable).")
@click.option("--pipeline-depth", default=1,   show_default=True,
              help="Chunks to hold in flight simultaneously (GPU miners: set > 1).")
@click.option("--parallel-exec",  is_flag=True, default=False,
              help="Execute multiple chunks in parallel threads (requires --pipeline-depth > 1).")
def mine(servers, identity, capabilities, volunteer, stake, cached_cids,
         pipeline_depth, parallel_exec):
    """Start a miner daemon.

    Identity is derived from an Ed25519 keypair — stable across restarts,
    portable across coordinators, requires no central authority.
    Auto-generated at ~/.unbound/identity.key on first run.

    Pass multiple --server URLs for automatic failover: if the active
    coordinator goes offline the miner connects to the next one immediately.

        unbound mine --server ws://node1:443 --server ws://node2:443
    """
    from ..miner.miner import Miner
    import logging
    logging.basicConfig(level=logging.INFO)

    miner = Miner(
        server_url=list(servers),
        identity_path=Path(identity) if identity else None,
        capabilities=list(capabilities),
        volunteer=volunteer,
        stake=stake,
        cached_cids=list(cached_cids),
        pipeline_depth=pipeline_depth,
        parallel_exec=parallel_exec,
    )
    click.echo(
        f"Miner {miner.miner_id}  servers:{list(servers)}  "
        f"caps:{list(capabilities)}"
        + ("  [volunteer]" if volunteer else f"  stake:{stake}")
    )
    asyncio.run(miner.run())


# ── Submit ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("program", type=click.Path(exists=True))
@click.option("--from",    "submitter",   required=True, help="Submitter address")
@click.option("--payment", default=100,   show_default=True, help="UBD to pay")
@click.option("--api",     default=API_URL, show_default=True)
def submit(program, submitter, payment, api):
    """Compile and submit a Python program as a job."""
    source = open(program).read()
    resp = requests.post(f"{api}/compile", json={"source": source})
    if resp.status_code != 200:
        click.echo(f"Compile error: {resp.text}", err=True)
        sys.exit(1)
    compiled = resp.json()
    resp = requests.post(f"{api}/jobs", json={
        "submitter": submitter, "chunks": compiled["chunks"],
        "payment": payment, "description": program,
    })
    if resp.status_code != 200:
        click.echo(f"Submit error: {resp.text}", err=True)
        sys.exit(1)
    data = resp.json()
    click.echo(f"Job submitted: {data['job_id']}")
    click.echo(f"  Chunks: {data['total_chunks']}")
    click.echo(f"  Payment locked: {data['payment_locked']} UBD")


# ── Result ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("job_id")
@click.option("--wait", is_flag=True, default=False, help="Wait until job completes")
@click.option("--api",  default=API_URL, show_default=True)
def result(job_id, wait, api):
    """Retrieve job results."""
    while True:
        resp = requests.get(f"{api}/jobs/{job_id}")
        if resp.status_code == 404:
            click.echo("Job not found", err=True)
            sys.exit(1)
        data   = resp.json()
        status = data["status"]
        done   = data["completed_chunks"]
        total  = data["total_chunks"]
        click.echo(f"Status: {status}  ({done}/{total} chunks)")
        if status == "completed":
            click.echo(f"Results: {data['results']}")
            break
        if not wait:
            break
        time.sleep(2)


# ── Balance ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("address")
@click.option("--api", default=API_URL, show_default=True)
def balance(address, api):
    """Check UBD balance for an address."""
    resp = requests.get(f"{api}/balance/{address}")
    data = resp.json()
    click.echo(f"{address}: {data['balance']} UBD")


# ── Faucet (dev only) ─────────────────────────────────────────────────────────

@cli.command()
@click.argument("address")
@click.option("--amount", default=1000, show_default=True)
@click.option("--db",     default="unbound.db", show_default=True)
def faucet(address, amount, db):
    """Credit test UBD to an address (dev/test only)."""
    from ..ledger.ledger import Ledger
    ledger = Ledger(db)
    ledger.credit(address, amount, "faucet")
    click.echo(f"Credited {amount} UBD to {address}. Balance: {ledger.balance(address)}")


# ── Batch (offline mode) ─────────────────────────────────────────────────────

@cli.group()
def batch():
    """Offline batch mode — operate without any network connection.

    Export jobs to a signed bundle file (.ubatch), execute them on any
    machine, import results back when any channel reopens.

    Transfer the bundle by any means: USB drive, QR code, radio, courier.

      unbound batch export --job <id> --out jobs.ubatch
      unbound batch run jobs.ubatch --out results.uresult
      unbound batch import results.uresult
    """


@batch.command("export")
@click.option("--job",      "job_ids", multiple=True, required=True, metavar="JOB_ID",
              help="Job ID to include (repeatable).")
@click.option("--out",      default="jobs.ubatch", show_default=True,
              help="Output bundle file.")
@click.option("--db",       default="unbound.db", show_default=True)
@click.option("--identity", default=None, metavar="PATH",
              help="Path to identity key for signing.")
def batch_export(job_ids, out, db, identity):
    """Export jobs from a running node into a signed offline bundle."""
    from ..registry.registry import Registry
    from ..net.offline import export_batch
    from ..net import identity as _id

    # We need access to the live registry — for now load a minimal one from db
    # In practice this would hook into the running node's registry.
    # For CLI use, re-create registry state is not yet supported;
    # use the Python API (offline.export_batch) from within a running node.
    click.echo(
        "Note: batch export requires a running node. "
        "Call offline.export_batch() from within your node process, "
        "or use the /batch/export API endpoint (if enabled).",
        err=True,
    )
    click.echo(f"Would export jobs: {list(job_ids)} → {out}")


@batch.command("run")
@click.argument("bundle_file", type=click.Path(exists=True))
@click.option("--out",      default="results.uresult", show_default=True,
              help="Output result file.")
@click.option("--identity", default=None, metavar="PATH",
              help="Path to identity key (auto-generated if absent).")
def batch_run(bundle_file, out, identity):
    """Execute all chunks in a .ubatch bundle — no network needed.

    Run jobs on an air-gapped machine and produce a signed result file
    to return via any available channel.
    """
    from ..net.offline import run_batch

    bundle_bytes = Path(bundle_file).read_bytes()
    click.echo(f"Running batch {bundle_file}…")

    identity_path = Path(identity) if identity else None
    result_bytes  = run_batch(bundle_bytes, identity_path=identity_path)

    Path(out).write_bytes(result_bytes)
    click.echo(f"Results written to {out}")


@batch.command("import")
@click.argument("result_file", type=click.Path(exists=True))
@click.option("--db",  default="unbound.db", show_default=True)
@click.option("--api", default=API_URL, show_default=True)
def batch_import(result_file, db, api):
    """Import offline results back into the node via the API.

    Call this when any channel reopens after an offline run.
    """
    result_bytes = Path(result_file).read_bytes()
    import base64
    resp = requests.post(f"{api}/batch/import", json={
        "results": base64.b64encode(result_bytes).decode(),
    })
    if not resp.ok:
        click.echo(f"Import error: {resp.text}", err=True)
        sys.exit(1)
    data = resp.json()
    click.echo(f"Imported {data.get('recorded', 0)} chunk results from {result_file}")


# ── Cluster (no payment) ──────────────────────────────────────────────────────

@cli.group()
def cluster():
    """Run Unbound as a private compute cluster — no payment required.

      unbound cluster node          # start coordinator
      unbound cluster mine          # start a worker
      unbound cluster run FILE      # submit a job and wait for results
    """


@cluster.command("node")
@click.option("--api-port",  default=8000, show_default=True)
@click.option("--ws-port",   default=8765, show_default=True)
@click.option("--identity",  default=None, metavar="PATH",
              help="Path to Ed25519 identity key.")
@click.option("--peer",      "peers", multiple=True, metavar="WS_URL",
              help="Peer coordinator URL (repeatable).")
@click.option("--tls-cert",  default=None, metavar="PATH")
@click.option("--tls-key",   default=None, metavar="PATH")
def cluster_node(api_port, ws_port, identity, peers, tls_cert, tls_key):
    """Start a cluster coordinator (no ledger, no payment)."""
    import uvicorn
    import threading

    from ..registry.registry import Registry
    from ..network.server import NodeServer
    from ..api.app import app, init as api_init

    registry = Registry()
    server   = NodeServer(
        registry, chain=None, ledger=None,
        ws_port=ws_port,
        identity_path=Path(identity) if identity else None,
        peers=list(peers) or None,
        tls_cert=tls_cert,
        tls_key=tls_key,
    )
    api_init(registry, ledger=None)

    click.echo(
        f"Cluster {server.node_id[:12]}…  "
        f"API:{api_port}  WS:{ws_port}  (no payment)"
        + (f"  peers:{len(peers)}" if peers else "")
    )

    def run_ws():
        asyncio.run(server.start())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()
    uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")


@cluster.command("mine")
@click.option("--server",     "servers",     multiple=True, default=[WS_URL],
              show_default=True, metavar="WS_URL",
              help="Coordinator URL (repeatable for failover).")
@click.option("--identity",   default=None,  metavar="PATH",
              help="Path to Ed25519 identity key.")
@click.option("--capability", "capabilities", multiple=True)
@click.option("--cached-cid", "cached_cids",  multiple=True)
def cluster_mine(servers, identity, capabilities, cached_cids):
    """Start a cluster worker."""
    from ..miner.miner import Miner
    import logging
    logging.basicConfig(level=logging.INFO)
    miner = Miner(
        server_url=list(servers),
        identity_path=Path(identity) if identity else None,
        capabilities=list(capabilities),
        cached_cids=list(cached_cids),
    )
    click.echo(f"Worker {miner.miner_id}  servers:{list(servers)}")
    asyncio.run(miner.run())


@cluster.command("run")
@click.argument("program", type=click.Path(exists=True))
@click.option("--api",        default=API_URL, show_default=True)
@click.option("--wait/--no-wait", default=True, show_default=True)
def cluster_run(program, api, wait):
    """Compile and run a program on the cluster, print results."""
    source = open(program).read()
    resp = requests.post(f"{api}/compile", json={"source": source})
    if resp.status_code != 200:
        click.echo(f"Compile error: {resp.text}", err=True)
        sys.exit(1)
    compiled = resp.json()
    resp = requests.post(f"{api}/jobs", json={
        "chunks": compiled["chunks"], "description": program,
    })
    if resp.status_code != 200:
        click.echo(f"Submit error: {resp.text}", err=True)
        sys.exit(1)
    job_id = resp.json()["job_id"]
    click.echo(f"Job {job_id} submitted ({len(compiled['chunks'])} chunks)")
    if not wait:
        return
    while True:
        resp = requests.get(f"{api}/jobs/{job_id}")
        data = resp.json()
        if data["status"] == "completed":
            click.echo(f"Results: {data['results']}")
            break
        time.sleep(1)


if __name__ == "__main__":
    cli()
