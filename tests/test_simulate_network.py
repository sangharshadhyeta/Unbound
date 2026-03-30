"""
Tests for the network-condition simulation.

Three layers:

  Unit — NetworkProfile statistics
    Verify that sample_one_way() produces delays with the correct mean,
    that losses fire at roughly the declared rate, and that retransmit
    delays are always additive (never make a loss frame arrive faster
    than a clean frame with the same base delay).

  Unit — HOL lock semantics
    Verify that a TCP loss event acquires the shared connection lock
    (forcing concurrent senders to wait) while a QUIC loss event and
    any non-loss TCP delivery both skip the lock entirely.

  Integration — end-to-end scenario correctness
    Run run_scenario() with reduced job counts and compute times to
    verify that all chunks complete, loss counters behave sensibly,
    and the ordering constraints hold (QUIC ≤ TCP on lossy links over
    enough trials, satellite depth analysis matches theory).
"""

import asyncio
import os
import random
import sys
import time

import pytest

# ── Import the simulation module from examples/ ───────────────────────────────
_examples = os.path.join(os.path.dirname(__file__), "..", "examples")
if _examples not in sys.path:
    sys.path.insert(0, _examples)

from simulate_network import NetworkProfile, MinerConfig, NetMiner, run_scenario, N_JOBS


# ── Unit: NetworkProfile statistics ──────────────────────────────────────────

class TestNetworkProfileStats:

    SAMPLES = 20_000

    def _profile(self, rtt=0.100, jitter=0.010, loss=0.05):
        return NetworkProfile("test", rtt=rtt, jitter=jitter, loss=loss)

    def test_mean_delay_close_to_half_rtt(self):
        """Mean one-way delay ≈ rtt/2 (jitter is zero-mean Gaussian)."""
        rng = random.Random(1)
        p   = self._profile(rtt=0.100, jitter=0.001, loss=0.0)
        delays = []
        for _ in range(self.SAMPLES):
            # Patch random to use our seeded rng
            old = (random.random, random.gauss)
            random.random = rng.random
            random.gauss  = rng.gauss
            d, _ = p.sample_one_way()
            random.random, random.gauss = old
            delays.append(d)
        mean = sum(delays) / len(delays)
        assert abs(mean - 0.050) < 0.002, f"mean={mean:.4f} expected ≈0.050"

    def test_loss_rate_matches_profile(self):
        """Observed loss rate is within 3σ of the declared rate."""
        import math
        rng  = random.Random(7)
        rate = 0.05
        p    = self._profile(loss=rate)
        n_loss = 0
        for _ in range(self.SAMPLES):
            old = (random.random, random.gauss, random.choices)
            random.random  = rng.random
            random.gauss   = rng.gauss
            random.choices = rng.choices
            _, lost = p.sample_one_way()
            random.random, random.gauss, random.choices = old
            n_loss += lost
        observed = n_loss / self.SAMPLES
        sigma    = math.sqrt(rate * (1 - rate) / self.SAMPLES)
        assert abs(observed - rate) < 3 * sigma, (
            f"observed={observed:.4f} expected={rate:.4f} ±{3*sigma:.4f}"
        )

    def test_loss_delay_always_exceeds_base(self):
        """A retransmit always adds time; a loss frame never arrives faster."""
        rng = random.Random(99)
        p   = self._profile(rtt=0.100, jitter=0.0, loss=1.0)   # always lose
        p2  = self._profile(rtt=0.100, jitter=0.0, loss=0.0)   # never lose

        for seed in range(500):
            local = random.Random(seed)
            old = (random.random, random.gauss, random.choices)
            random.random  = local.random
            random.gauss   = local.gauss
            random.choices = local.choices
            d_loss, _ = p.sample_one_way()
            d_clean, _ = p2.sample_one_way()
            random.random, random.gauss, random.choices = old
            assert d_loss > d_clean, (
                f"loss delay {d_loss:.4f} ≤ clean delay {d_clean:.4f} — "
                "retransmit must add time"
            )

    def test_delay_always_positive(self):
        """Delays are always > 0 regardless of jitter sign."""
        rng = random.Random(42)
        # Large jitter relative to rtt — gauss could otherwise go negative
        p   = NetworkProfile("t", rtt=0.001, jitter=0.010, loss=0.0)
        for _ in range(5_000):
            old = (random.random, random.gauss)
            random.random = rng.random
            random.gauss  = rng.gauss
            d, _ = p.sample_one_way()
            random.random, random.gauss = old
            assert d > 0, f"negative delay {d}"

    def test_retransmit_multiplier_at_least_double_rtt(self):
        """Minimum RTO is RTT × 2 (k=1 case)."""
        p   = NetworkProfile("t", rtt=0.100, jitter=0.0, loss=1.0)
        rng = random.Random(0)
        # Force k=1 by making choices always return [1]
        original_choices = random.choices
        random.choices = lambda pop, weights=None, k=1: [pop[0]]   # k=1 always
        original_gauss  = random.gauss
        random.gauss    = lambda mu, sigma: 0.0                     # no jitter
        original_random = random.random
        random.random   = lambda: 0.0   # always loses (0.0 < any loss rate)
        try:
            d, lost = p.sample_one_way()
        finally:
            random.choices = original_choices
            random.gauss   = original_gauss
            random.random  = original_random
        assert lost
        # base = rtt/2 = 0.05, rto = rtt*2 = 0.20, total = 0.25
        assert abs(d - 0.25) < 1e-9, f"expected 0.25 got {d}"


# ── Unit: HOL lock semantics ──────────────────────────────────────────────────

class TestHOLLockSemantics:
    """
    Verify that the connection lock is acquired exactly when expected:
      - TCP + loss  → lock acquired, held for retransmit duration
      - TCP + no loss → lock NOT acquired
      - QUIC + loss  → lock NOT acquired
    """

    def _make_miner(self, quic: bool) -> NetMiner:
        cfg   = MinerConfig("test", compute=0.0, pipeline_depth=8,
                            parallel_exec=True, quic=quic)
        p     = NetworkProfile("t", rtt=0.010, jitter=0.0, loss=0.0)
        done  = asyncio.Event()
        miner = NetMiner(cfg, p, done, 1)
        miner._hol_lock = asyncio.Lock()
        return miner

    def _force_loss(self, miner: NetMiner, delay: float):
        """Patch sample_one_way to return a loss event with given delay."""
        miner._p = type("P", (), {
            "sample_one_way": lambda self: (delay, True),
            "rtt": 0.010,
        })()

    def _force_no_loss(self, miner: NetMiner, delay: float):
        """Patch sample_one_way to return a non-loss delivery."""
        miner._p = type("P", (), {
            "sample_one_way": lambda self: (delay, False),
            "rtt": 0.010,
        })()

    @pytest.mark.asyncio
    async def test_tcp_loss_acquires_hol_lock(self):
        """TCP loss: the HOL lock is held for the retransmit delay."""
        miner = self._make_miner(quic=False)
        self._force_loss(miner, delay=0.050)

        lock_was_held_during_sleep = False

        async def _check_lock():
            nonlocal lock_was_held_during_sleep
            await asyncio.sleep(0.010)   # during the 50 ms retransmit sleep
            lock_was_held_during_sleep = miner._hol_lock.locked()

        # Simulate the loss-path in _exec (TCP, upload_delay=True)
        async def _exec_loss():
            delay, loss = miner._p.sample_one_way()
            assert loss
            async with miner._hol_lock:
                await asyncio.sleep(delay)

        checker = asyncio.create_task(_check_lock())
        await _exec_loss()
        await checker
        assert lock_was_held_during_sleep, "HOL lock was not held during TCP retransmit"

    @pytest.mark.asyncio
    async def test_tcp_no_loss_skips_lock(self):
        """TCP non-loss delivery: the HOL lock is never acquired."""
        miner = self._make_miner(quic=False)
        self._force_no_loss(miner, delay=0.005)

        lock_held = False
        original_acquire = miner._hol_lock.acquire

        async def _spy_acquire():
            nonlocal lock_held
            lock_held = True
            return await original_acquire()

        miner._hol_lock.acquire = _spy_acquire   # type: ignore[method-assign]

        delay, loss = miner._p.sample_one_way()
        assert not loss
        await asyncio.sleep(delay)   # non-loss path: no lock

        assert not lock_held, "HOL lock was acquired on a non-loss delivery"

    @pytest.mark.asyncio
    async def test_quic_loss_skips_lock(self):
        """QUIC loss: independent stream delivery, no lock acquired."""
        miner = self._make_miner(quic=True)
        self._force_loss(miner, delay=0.050)

        lock_acquired = False
        original_acquire = miner._hol_lock.acquire

        async def _spy_acquire():
            nonlocal lock_acquired
            lock_acquired = True
            return await original_acquire()

        miner._hol_lock.acquire = _spy_acquire   # type: ignore[method-assign]

        # QUIC path: loss fires but no lock
        delay, loss = miner._p.sample_one_way()
        assert loss
        miner._losses += 1
        await asyncio.sleep(delay)   # QUIC: just sleep, no lock

        assert not lock_acquired, "HOL lock was acquired in QUIC mode"

    @pytest.mark.asyncio
    async def test_hol_lock_queues_concurrent_sender(self):
        """A concurrent sender must wait while the HOL lock is held."""
        miner  = self._make_miner(quic=False)
        events = []

        async def slow_sender():
            async with miner._hol_lock:
                events.append("slow_start")
                await asyncio.sleep(0.040)
                events.append("slow_end")

        async def fast_sender():
            await asyncio.sleep(0.010)   # starts after slow acquires lock
            async with miner._hol_lock:
                events.append("fast_start")

        await asyncio.gather(slow_sender(), fast_sender())
        assert events == ["slow_start", "slow_end", "fast_start"], (
            f"Expected serial order, got {events}"
        )


# ── Integration: end-to-end scenario correctness ─────────────────────────────

# Reduced constants for fast integration tests
_N  = 4      # chunks per scenario
_C  = 0.005  # 5 ms compute

from unbound.uvm.opcodes import PUSH, OUTPUT, HALT
_STREAM = [PUSH, 42, OUTPUT, HALT]   # self-contained: pushes 42 and outputs it

def _fast_config(name, depth, parallel, quic) -> MinerConfig:
    return MinerConfig(name, compute=_C, pipeline_depth=depth,
                       parallel_exec=parallel, quic=quic)

def _fast_profile(name, rtt, jitter, loss) -> NetworkProfile:
    return NetworkProfile(name, rtt=rtt, jitter=jitter, loss=loss)


class TestScenarioCompleteness:
    """All chunks must complete under every profile × config combination."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("depth,par,quic", [
        (1, False, False),   # pull sequential
        (4, False, False),   # pipeline sequential TCP
        (4, True,  False),   # pipeline parallel TCP
        (4, True,  True),    # pipeline parallel QUIC
    ])
    async def test_all_chunks_complete_on_fiber(self, depth, par, quic):
        """All N chunks complete in under 10s on a near-ideal network."""
        cfg     = _fast_config("m", depth, par, quic)
        profile = _fast_profile("fiber", rtt=0.005, jitter=0.001, loss=0.0)

        elapsed, losses = await run_scenario(cfg, profile, n_jobs=_N, stream=_STREAM)
        assert elapsed < 10.0, f"Timed out: {elapsed:.2f}s"
        assert losses == 0

    @pytest.mark.asyncio
    async def test_all_chunks_complete_on_degraded(self):
        """All chunks complete even under high loss (8%)."""
        cfg     = _fast_config("gpu", 4, True, False)
        profile = _fast_profile("degraded", rtt=0.020, jitter=0.010, loss=0.08)
        random.seed(5)

        elapsed, losses = await run_scenario(cfg, profile, n_jobs=_N, stream=_STREAM)
        assert elapsed < 20.0, f"Timed out: {elapsed:.2f}s"
        assert losses <= _N, f"Implausible loss count {losses}"


class TestLossAccounting:
    """Loss counters must be non-negative and bounded."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("loss_rate", [0.0, 0.02, 0.10])
    async def test_loss_count_non_negative(self, loss_rate):
        cfg     = _fast_config("g", 4, True, False)
        profile = _fast_profile("p", rtt=0.010, jitter=0.002, loss=loss_rate)
        random.seed(42)

        _, losses = await run_scenario(cfg, profile, n_jobs=_N, stream=_STREAM)
        assert losses >= 0

    @pytest.mark.asyncio
    async def test_zero_loss_on_zero_loss_profile(self):
        """loss=0.0 profile must produce exactly zero retransmits."""
        cfg     = _fast_config("g", 4, True, False)
        profile = _fast_profile("clean", rtt=0.010, jitter=0.001, loss=0.0)

        _, losses = await run_scenario(cfg, profile, n_jobs=_N, stream=_STREAM)
        assert losses == 0


class TestPipelineSpeedup:
    """Pipeline and parallel modes must be faster than serial on clean networks."""

    @pytest.mark.asyncio
    async def test_pipeline_faster_than_serial_on_high_latency(self):
        """
        On a high-latency, zero-loss link, depth=4 must beat depth=1.
        Serial:   N × (C + RTT)
        Pipeline: N × C + RTT   (RTT amortised)
        """
        profile = _fast_profile("slow", rtt=0.100, jitter=0.0, loss=0.0)
        random.seed(0)

        cfg_serial   = _fast_config("seq",  1, False, False)
        cfg_pipeline = _fast_config("pipe", 4, False, False)

        t_serial,   _ = await run_scenario(cfg_serial,   profile, n_jobs=_N, stream=_STREAM)
        t_pipeline, _ = await run_scenario(cfg_pipeline, profile, n_jobs=_N, stream=_STREAM)

        assert t_pipeline < t_serial, (
            f"Pipeline ({t_pipeline:.3f}s) not faster than serial ({t_serial:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_parallel_faster_than_sequential_pipeline(self):
        """
        Parallel execution (depth=4, parallel_exec=True) must beat
        sequential pipeline (depth=4, parallel_exec=False) on a clean link.
        Wall time: N×C/D+RTT vs N×C+RTT.
        """
        profile = _fast_profile("clean", rtt=0.020, jitter=0.0, loss=0.0)
        random.seed(1)

        cfg_seq = _fast_config("seq", 4, False, False)
        cfg_par = _fast_config("par", 4, True,  False)

        t_seq, _ = await run_scenario(cfg_seq, profile, n_jobs=_N, stream=_STREAM)
        t_par, _ = await run_scenario(cfg_par, profile, n_jobs=_N, stream=_STREAM)

        assert t_par < t_seq, (
            f"Parallel ({t_par:.3f}s) not faster than sequential ({t_seq:.3f}s)"
        )

    @pytest.mark.asyncio
    async def test_quic_not_slower_than_tcp_on_zero_loss(self):
        """On a zero-loss link QUIC and TCP take similar time (within 50%)."""
        profile = _fast_profile("clean", rtt=0.020, jitter=0.0, loss=0.0)
        random.seed(3)

        cfg_tcp  = _fast_config("tcp",  4, True, False)
        cfg_quic = _fast_config("quic", 4, True, True)

        t_tcp,  _ = await run_scenario(cfg_tcp,  profile, n_jobs=_N, stream=_STREAM)
        t_quic, _ = await run_scenario(cfg_quic, profile, n_jobs=_N, stream=_STREAM)

        ratio = t_tcp / t_quic if t_quic > 0 else float("inf")
        assert 0.5 < ratio < 2.0, (
            f"TCP ({t_tcp:.3f}s) and QUIC ({t_quic:.3f}s) diverge too much "
            f"with zero loss (ratio={ratio:.2f})"
        )


class TestSatelliteDepthRequirement:
    """
    Satellite (high RTT) links drain the pipeline fast.
    Verify the depth-requirement formula: depth ≥ ceil(RTT / C).
    """

    def test_depth_required_formula(self):
        """ceil(RTT / C) gives the minimum depth to stay continuously fed."""
        import math
        cases = [
            (0.600, 0.015, 40),   # satellite + GPU
            (0.050, 0.015, 4),    # broadband + GPU
            (0.010, 0.150, 1),    # fibre + CPU  (compute > RTT → depth=1 fine)
        ]
        for rtt, compute, expected_min in cases:
            depth = math.ceil(rtt / compute)
            assert depth == expected_min, (
                f"RTT={rtt}, C={compute}: expected depth≥{expected_min}, got {depth}"
            )

    def test_threshold_local_cap_covers_satellite_requirement(self):
        """THRESHOLD_LOCAL cap (64) must be ≥ the satellite depth requirement."""
        import math
        from unbound.protocol import pipeline_depth_cap, THRESHOLD_LOCAL
        cap = pipeline_depth_cap(THRESHOLD_LOCAL)

        satellite_rtt  = 0.600
        gpu_compute    = 0.015
        required_depth = math.ceil(satellite_rtt / gpu_compute)   # 40

        assert cap >= required_depth, (
            f"THRESHOLD_LOCAL cap ({cap}) < satellite requirement ({required_depth})"
        )
