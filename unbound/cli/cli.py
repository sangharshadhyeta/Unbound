"""
Unbound CLI

Commands:
  unbound node      -- start a full node (API + WebSocket server)
  unbound mine      -- start a miner daemon
  unbound submit    -- compile and submit a job
  unbound result    -- poll for and retrieve job results
  unbound balance   -- check UBD balance
  unbound faucet    -- credit test UBD to an address (dev only)
"""

import asyncio
import json
import sys
import time

import click
import requests


API_URL = "http://localhost:8000"
WS_URL  = "ws://localhost:8765"


@click.group()
def cli():
    """Unbound — Proof of Useful Work cryptocurrency."""


# ── Node ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--api-port",      default=8000,  show_default=True)
@click.option("--ws-port",       default=8765,  show_default=True)
@click.option("--db",            default="unbound.db", show_default=True)
@click.option("--min-stake",     default=10,    show_default=True,
              help="Minimum UBD a paid volunteer must lock to mine. "
                   "Calibrate to current market rate — higher price = lower number.")
@click.option("--slash-fraction", default=0.25, show_default=True,
              help="Fraction of chunk reward burned on invalid result (0.0–1.0). "
                   "Scales automatically with job payment size.")
def node(api_port, ws_port, db, min_stake, slash_fraction):
    """Start a full Unbound node (API + WebSocket server)."""
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
    server   = NodeServer(
        registry, chain, ledger,
        ws_port=ws_port,
        min_stake=min_stake,
        slash_fraction=slash_fraction,
    )

    api_init(registry, ledger)

    def run_ws():
        asyncio.run(server.start())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()

    click.echo(
        f"Node running — API:{api_port}  WS:{ws_port}  DB:{db}  "
        f"min_stake:{min_stake} UBD  slash:{int(slash_fraction * 100)}%"
    )
    uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")


# ── Mine ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--id",         "miner_id",    default=None, help="Miner ID (auto-generated if omitted)")
@click.option("--server",     default=WS_URL, show_default=True)
@click.option("--capability", "capabilities", multiple=True, help="Declare a capability tag (repeatable). e.g. --capability gpu --capability cuda12")
@click.option("--volunteer",  is_flag=True,   default=False, help="Contribute compute freely — no UBD earned. Like BOINC: anyone can help.")
def mine(miner_id, server, capabilities, volunteer):
    """Start a miner daemon.

    By default earns UBD per verified chunk. Use --volunteer to contribute
    freely without earning — useful for hobbyists, students, or anyone who
    wants to support the network from any hardware.
    """
    from ..miner.miner import Miner
    import logging
    logging.basicConfig(level=logging.INFO)
    miner = Miner(
        miner_id=miner_id,
        server_url=server,
        capabilities=list(capabilities),
        volunteer=volunteer,
    )
    if volunteer:
        click.echo(f"Contributing freely to the Unbound network — no UBD earned")
    click.echo(f"Starting miner {miner.miner_id} → {server}  caps={list(capabilities)}")
    asyncio.run(miner.run())


# ── Submit ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("program", type=click.Path(exists=True))
@click.option("--from",    "submitter",   required=True, help="Submitter address")
@click.option("--payment", default=100,   show_default=True, help="UBD to pay")
@click.option("--api",     default=API_URL, show_default=True)
def submit(program, submitter, payment, api):
    """Compile and submit a Python program as a job."""
    source = open(program).read()

    # Step 1: compile source → binary chunks
    resp = requests.post(f"{api}/compile", json={"source": source})
    if resp.status_code != 200:
        click.echo(f"Compile error: {resp.text}", err=True)
        sys.exit(1)
    compiled = resp.json()

    # Step 2: submit binary chunks
    resp = requests.post(f"{api}/jobs", json={
        "submitter": submitter,
        "chunks":    compiled["chunks"],
        "payment":   payment,
        "description": program,
    })
    if resp.status_code != 200:
        click.echo(f"Submit error: {resp.text}", err=True)
        sys.exit(1)
    data = resp.json()
    click.echo(f"Job submitted: {data['job_id']}")
    click.echo(f"  Chunks: {data['total_chunks']}")
    click.echo(f"  Payment locked: {data['payment_locked']} UBD")


# ── Result ────────────────────────────────────────────────────────────

@cli.command()
@click.argument("job_id")
@click.option("--wait",  is_flag=True, default=False, help="Wait until job completes")
@click.option("--api",   default=API_URL, show_default=True)
def result(job_id, wait, api):
    """Retrieve job results."""
    while True:
        resp = requests.get(f"{api}/jobs/{job_id}")
        if resp.status_code == 404:
            click.echo("Job not found", err=True)
            sys.exit(1)
        data = resp.json()
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


# ── Balance ───────────────────────────────────────────────────────────

@cli.command()
@click.argument("address")
@click.option("--api", default=API_URL, show_default=True)
def balance(address, api):
    """Check UBD balance for an address."""
    resp = requests.get(f"{api}/balance/{address}")
    data = resp.json()
    click.echo(f"{address}: {data['balance']} UBD")


# ── Faucet (dev only) ─────────────────────────────────────────────────

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


# ── Cluster (no payment) ──────────────────────────────────────────────

@cli.group()
def cluster():
    """Run Unbound as a private compute cluster — no payment required.

    Use this when you want to aggregate compute resources across multiple
    machines without a cryptocurrency layer. Workers connect to the
    coordinator and execute chunks for free.

      unbound cluster node          # start coordinator
      unbound cluster mine          # start a worker
      unbound cluster run FILE      # submit a job and wait for results
    """


@cluster.command("node")
@click.option("--api-port", default=8000, show_default=True)
@click.option("--ws-port",  default=8765, show_default=True)
def cluster_node(api_port, ws_port):
    """Start a cluster coordinator (no ledger, no payment)."""
    import uvicorn
    import threading

    from ..registry.registry import Registry
    from ..network.server import NodeServer
    from ..api.app import app, init as api_init

    registry = Registry()
    server   = NodeServer(registry, chain=None, ledger=None, ws_port=ws_port)

    api_init(registry, ledger=None)

    def run_ws():
        asyncio.run(server.start())

    t = threading.Thread(target=run_ws, daemon=True)
    t.start()

    click.echo(f"Cluster coordinator — API:{api_port}  WS:{ws_port}  (no payment)")
    uvicorn.run(app, host="0.0.0.0", port=api_port, log_level="warning")


@cluster.command("mine")
@click.option("--id",         "miner_id",    default=None)
@click.option("--server",     default=WS_URL, show_default=True)
@click.option("--capability", "capabilities", multiple=True, help="Declare a capability tag (repeatable). e.g. --capability gpu --capability high-memory")
def cluster_mine(miner_id, server, capabilities):
    """Start a cluster worker (connects to coordinator, executes chunks)."""
    from ..miner.miner import Miner
    import logging
    logging.basicConfig(level=logging.INFO)
    miner = Miner(miner_id=miner_id, server_url=server, capabilities=list(capabilities))
    click.echo(f"Cluster worker {miner.miner_id} → {server}  caps={list(capabilities)}")
    asyncio.run(miner.run())


@cluster.command("run")
@click.argument("program", type=click.Path(exists=True))
@click.option("--api", default=API_URL, show_default=True)
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
        "chunks":      compiled["chunks"],
        "description": program,
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
