"""
Protocol-level privacy constants and helpers.

The dispersal privacy bound (whitepaper §3.4):
  For a job split into n chunks with job-exclusion enforced, a single miner
  holds at most 1 chunk, so leakage ≤ (1/n) · H(X).

  A miner with pipeline_depth D holds D chunks from D different jobs.
  For jobs with exactly n chunks each, aggregate in-flight exposure is
  D × (1/n).  Capping D at n keeps aggregate exposure ≤ 1 full job.

  Therefore: MAX_PIPELINE_DEPTH = ceil(1 / privacy_threshold)
  where privacy_threshold is the maximum acceptable fraction of any one job
  a single miner may observe.

Choosing a threshold
---------------------
Use pipeline_depth_cap(threshold) to derive the server / miner cap.
Three named presets cover the common cases:

  THRESHOLD_PUBLIC   = 0.125   (12.5%)
    Public untrusted network.  Each job must have ≥ 8 chunks.
    A miner in a 1000-node network sees at most 0.125% of aggregate work.
    Recommended when miners are anonymous and unvetted.

  THRESHOLD_INTERNAL = 0.25    (25%)
    Internal or semi-trusted network (team cluster, corporate VPN).
    Jobs require ≥ 4 chunks.  Higher pipeline depth → better GPU utilisation.
    Recommended when miners are known employees or vetted contractors.

  THRESHOLD_LOCAL    = 1.0     (100%)
    Local cluster — all machines are owned and trusted.
    No meaningful privacy constraint from chunking; AMP masking still applies
    if the submitter wants numeric privacy from sysadmins.
    No minimum job size enforced; pipeline depth unconstrained (capped at 64
    as a practical socket-buffer limit, not a privacy limit).
    Recommended for private HPC / research clusters.

If none of the presets fits, pass any float in (0, 1] to pipeline_depth_cap().
"""

from math import ceil


# ── Named presets ─────────────────────────────────────────────────────────────

THRESHOLD_PUBLIC   = 0.125   # public untrusted network   → cap = 8
THRESHOLD_INTERNAL = 0.25    # internal / semi-trusted    → cap = 4
THRESHOLD_LOCAL    = 1.0     # local cluster, no privacy constraint

# Default for servers and miners that don't specify a threshold
DEFAULT_THRESHOLD  = THRESHOLD_PUBLIC


# ── Practical upper limit for local/unconstrained deployments ─────────────────
# Not a privacy limit — just a reasonable socket / scheduler ceiling.
_LOCAL_DEPTH_CAP = 64


def pipeline_depth_cap(threshold: float) -> int:
    """
    Return the maximum pipeline_depth that satisfies the given privacy threshold.

    threshold : float in (0, 1]
        Maximum fraction of any single job's input a miner may observe.
        Use one of the THRESHOLD_* presets or supply your own value.

    Returns an int ≥ 1.  For threshold ≥ 1.0 returns _LOCAL_DEPTH_CAP (64)
    — there is no privacy-derived constraint, only a practical socket limit.

    Examples
    --------
    >>> pipeline_depth_cap(THRESHOLD_PUBLIC)    # 0.125 → 8
    8
    >>> pipeline_depth_cap(THRESHOLD_INTERNAL)  # 0.25  → 4
    4
    >>> pipeline_depth_cap(THRESHOLD_LOCAL)     # 1.0   → 64
    64
    >>> pipeline_depth_cap(0.5)                 # 50%   → 2
    2
    """
    if threshold <= 0:
        raise ValueError("privacy_threshold must be > 0")
    if threshold >= 1.0:
        return _LOCAL_DEPTH_CAP
    return max(1, ceil(1 / threshold))


def recommend_threshold(mode: str) -> float:
    """
    Return a recommended privacy threshold for a named deployment mode.

    mode : one of "public", "internal", "local"

    Raises ValueError for unknown modes.
    """
    modes = {
        "public":   THRESHOLD_PUBLIC,
        "internal": THRESHOLD_INTERNAL,
        "local":    THRESHOLD_LOCAL,
    }
    if mode not in modes:
        raise ValueError(
            f"Unknown mode {mode!r}. Choose from: {', '.join(modes)}"
        )
    return modes[mode]


# ── Backwards-compatible default cap ─────────────────────────────────────────
# Used by components that have not yet been updated to accept a threshold param.
MAX_PIPELINE_DEPTH = pipeline_depth_cap(DEFAULT_THRESHOLD)
