"""
Example 4: Linear Regression via Distributed Gradient Estimation
================================================================

Problem:   train a linear model  y = w0 + w1*x  to fit noisy data
Sequential: gradient descent — compute gradient, update weights, repeat

Conversion to search problem
-----------------------------
  Search space:   weight perturbations W + ε·eᵢ  (one per dimension × data slice)
  Candidate:      (weight_dimension, data_point) pair
  Evaluation:     L(W + ε·eᵢ, xⱼ, yⱼ) — loss for one perturbation on one sample
  Miner does:     evaluate squared error at perturbed weights for one sample
  Pool does:      collect all loss values; aggregator computes gradient

Training loop:
  1. Compute baseline loss L(W) locally (cheap, one pass)
  2. Dispatch n_dims × n_data chunks → miners evaluate L(W + ε·eᵢ, xⱼ)
  3. Aggregate: grad[i] = (mean_perturbed_loss_i - baseline) / ε
  4. Update: W = W - lr × grad
  5. Repeat until convergence

This is finite-difference gradient estimation (also called "zero-order optimization").
Same idea as Evolution Strategies (OpenAI, 2017) — gradient-free, massively parallel.

Fixed-point arithmetic
-----------------------
The UVM works with integers only. Floats are encoded as:
  integer = int(float_value × SCALE)   where SCALE = 1000

  w0 = 0.5   →  w0_fp = 500
  x  = 1.2   →  x_fp  = 1200
  w0 * x     →  500 * 1200 // 1000 = 600   →  0.600  ✓

All values in UVM programs are fixed-point. The submitter encodes before sending
and decodes after receiving. Miners see only integers — no floats, no semantics.

Real-world equivalents
-----------------------
  - Train a neural network: each miner evaluates one weight perturbation on one mini-batch
  - Reinforcement learning: each miner runs one episode under one policy perturbation
  - Hyperparameter tuning: each miner trains under one configuration (see 05_optimize.py)
  - Any gradient-free optimization: CMA-ES, Nelder-Mead, Bayesian optimization
"""

from unbound.sdk import UnboundClient, GradientEstimator
from unbound.compiler.compiler import compile_source
from unbound.uvm.vm import UVM


SCALE = 1000  # fixed-point scale: 1.0 → 1000


# ── MSE loss kernel ────────────────────────────────────────────────────────────
# Computes squared error for one (x, y) sample at perturbed weights (w0, w1).
# Variables w0, w1, x, y, SCALE are injected by the job builder before this runs.
#
# Fixed-point MSE:
#   pred = w0 + w1*x // SCALE          (linear model)
#   err  = pred - y                    (error)
#   loss = err * err // SCALE          (squared error, scaled back)
#   print(loss)

MSE_LOSS = """
pred = w0 + w1 * x // SCALE
err = pred - y
loss = err * err // SCALE
print(loss)
"""


# ── Run locally ────────────────────────────────────────────────────────────────

def run_locally(n_steps: int = 200, lr: float = 0.005):
    print("=== Linear Regression via Distributed Gradient Estimation ===\n")

    # True line: y = 2x + 1, with noise
    import random
    random.seed(7)
    true_w0, true_w1 = 1.0, 2.0
    data = [(float(xi), true_w0 + true_w1 * xi + random.uniform(-0.5, 0.5))
            for xi in range(1, 11)]

    print(f"True model:  y = {true_w0} + {true_w1}·x")
    print(f"Training on: {len(data)} samples with noise\n")

    weights = [0.0, 0.0]  # start at zero
    vm = UVM()

    def evaluate_loss(w: list[float]) -> float:
        """Compute mean MSE at given weights locally."""
        W_fp = [int(wi * SCALE) for wi in w]
        total = 0
        for x, y in data:
            w0_fp, w1_fp = W_fp
            x_fp = int(x * SCALE)
            y_fp = int(y * SCALE)
            src = f"w0 = {w0_fp}\nw1 = {w1_fp}\nx = {x_fp}\ny = {y_fp}\nSCALE = {SCALE}\n{MSE_LOSS}"
            stream, _ = compile_source(src)
            result = vm.execute(stream)
            if result:
                total += result[0]
        return total / len(data) / SCALE

    print(f"{'Step':>4}  {'w0':>8}  {'w1':>8}  {'loss':>10}")
    print("-" * 38)

    for step in range(n_steps):
        job = GradientEstimator(
            loss_body=MSE_LOSS,
            weights=weights,
            data=data,
            epsilon=0.01,
            payment=100,
        )

        # Build chunks (normally sent to network — here we run them locally)
        chunks = job.build_chunks()
        raw_results = _run_chunks_locally(chunks, vm)

        gradient = job.aggregate(raw_results)
        weights = [w - lr * g for w, g in zip(weights, gradient)]

        if step % 40 == 0 or step == n_steps - 1:
            loss = evaluate_loss(weights)
            print(f"{step:>4}  {weights[0]:>8.4f}  {weights[1]:>8.4f}  {loss:>10.4f}")

    print()
    print(f"Learned:  y = {weights[0]:.4f} + {weights[1]:.4f}·x")
    print(f"True:     y = {true_w0:.4f} + {true_w1:.4f}·x")
    print()
    print(f"Each step dispatched {len(data) * 2} chunks ({len(data)} samples × 2 weight dims).")
    print(f"Total chunks across {n_steps} steps: {n_steps * len(data) * 2}")
    print(f"\nOn the network: all {len(data) * 2} evaluations per step run in parallel.")
    print(f"Miner sees only: w0_fp, w1_fp, x_fp, y_fp, SCALE. Never knows these are")
    print(f"sensor readings, stock prices, or patient measurements.")


def _run_chunks_locally(chunks: list[bytes], vm: UVM) -> list[int]:
    """Execute compiled chunks directly in the local UVM (no node needed)."""
    from unbound.uvm.encoding import decode

    results = []
    for chunk_bytes in chunks:
        stream = decode(chunk_bytes)
        output = vm.execute(stream)
        results.append(output[0] if output else 0)
    return results


# ── Run on network ─────────────────────────────────────────────────────────────

def run_on_network(api_url: str = "http://localhost:8000", address: str = "alice",
                   n_steps: int = 5, lr: float = 0.001):
    import random
    random.seed(7)
    true_w0, true_w1 = 1.0, 2.0
    data = [(float(xi), true_w0 + true_w1 * xi + random.uniform(-0.5, 0.5))
            for xi in range(1, 11)]

    client = UnboundClient(api_url, address=address)
    weights = [0.0, 0.0]

    for step in range(n_steps):
        job = GradientEstimator(
            loss_body=MSE_LOSS,
            weights=weights,
            data=data,
            epsilon=0.01,
            payment=100,
        )
        gradient = client.run_job(job)
        weights = [w - lr * g for w, g in zip(weights, gradient)]
        print(f"Step {step}: w = [{weights[0]:.4f}, {weights[1]:.4f}]")

    print(f"\nFinal: y = {weights[0]:.4f} + {weights[1]:.4f}·x")


# ── Demonstrate fixed-point arithmetic ────────────────────────────────────────

def demonstrate_fixed_point():
    """Show how floats survive the integer-only UVM round-trip."""
    print("\n=== Fixed-Point Arithmetic Demo ===\n")

    # true values
    w0, w1 = 0.5, 2.0
    x,  y  = 0.3, 1.1

    # encode
    w0_fp = int(w0 * SCALE)   # 500
    w1_fp = int(w1 * SCALE)   # 2000
    x_fp  = int(x  * SCALE)   # 300
    y_fp  = int(y  * SCALE)   # 1100

    print(f"Float values:       w0={w0}, w1={w1}, x={x}, y={y}")
    print(f"Fixed-point (×{SCALE}): w0={w0_fp}, w1={w1_fp}, x={x_fp}, y={y_fp}")
    print()

    # what miner sees and computes
    src = f"w0 = {w0_fp}\nw1 = {w1_fp}\nx = {x_fp}\ny = {y_fp}\nSCALE = {SCALE}\n{MSE_LOSS}"
    stream, schema = compile_source(src)
    result = UVM().execute(stream)

    # decode
    loss_fp   = result[0]
    loss_float = loss_fp / SCALE

    # verify
    pred      = w0 + w1 * x
    expected  = (pred - y) ** 2

    print(f"Miner computes:     loss_fp = {loss_fp}")
    print(f"Decoded:            loss    = {loss_float:.4f}")
    print(f"Expected (float):   loss    = {expected:.4f}")
    print()
    print(f"Schema holds {len(schema.variables)} variable mappings.")
    print(f"Miner has no schema. Sees integers. Returns integer. Knows nothing.")


if __name__ == "__main__":
    run_locally()
    demonstrate_fixed_point()
