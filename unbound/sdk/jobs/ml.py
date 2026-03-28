"""
Unbound SDK — ML Jobs

Machine learning training converted to search problems.

The key insight: gradient descent searches for weights that minimize loss.
That search can be distributed — each miner evaluates loss at one perturbed
weight vector. The pool aggregates evaluations into a gradient estimate.
Every evaluation is useful. Nothing is wasted.

Two job types:

  GradientEstimator   — one round of distributed finite-difference gradient estimation
                        for training neural networks / linear models without backprop
  HyperparamSearch    — evaluate many hyperparameter configurations in parallel
                        to find the optimal settings for any model

Fixed-point arithmetic
----------------------
The UVM works with integers. Floats are represented as fixed-point:
  value_fp = int(value_float * SCALE)
  SCALE = 1000 by default → 3 decimal places of precision

  e.g.  w = 1.234  →  w_fp = 1234
        x = 0.5    →  x_fp = 500
        w * x      →  1234 * 500 // 1000 = 617  →  0.617
"""

from __future__ import annotations

SCALE = 1000  # fixed-point scale factor


class GradientEstimator:
    """
    One round of distributed finite-difference gradient estimation.

    Estimates ∇L(W) by evaluating L(W + ε·eᵢ) for each basis direction i,
    where each evaluation runs as an independent chunk on the miner network.

    Usage
    -----
    weights = [0.0, 0.0]        # initial model weights
    for step in range(100):
        job = GradientEstimator(
            loss_body   = MSE_LOSS,     # Python source, uses w0, w1, x, y, SCALE
            weights     = weights,
            data        = training_data,
            epsilon     = 0.01,
            payment     = 100,
        )
        chunks  = job.build_chunks()
        job_id  = client.submit(chunks, payment=job.payment)
        raw     = client.wait(job_id)
        gradient = job.aggregate(raw)
        weights  = [w - 0.01 * g for w, g in zip(weights, gradient)]

    Conversion pattern
    ------------------
    Gradient descent (sequential):
        for i in range(n_weights):
            grad[i] = (L(W + ε·eᵢ) - L(W)) / ε   ← one computation per weight dim

    Search formulation (parallel):
        each (weight_dim, data_slice) pair = one candidate
        miner evaluates L(W + ε·eᵢ, data_slice) for that one pair
        pool aggregates: mean over data slices → grad[i] for each i
    """

    def __init__(
        self,
        loss_body: str,
        weights: list[float],
        data: list[tuple[float, float]],
        epsilon: float = 0.01,
        payment: int = 100,
    ):
        """
        loss_body : Python source that uses variables w0, w1, ..., x, y, SCALE
                    and ends with print(loss_value_fixed_point)
        weights   : current model weights (floats)
        data      : list of (x, y) training pairs (floats)
        epsilon   : perturbation size for finite differences
        payment   : UBD to lock in escrow
        """
        self._loss_body = loss_body
        self._weights = weights
        self._data = data
        self._epsilon = epsilon
        self._payment = payment

        # Track chunk structure for aggregation:
        # chunks are grouped as: [dim0_slice0, dim0_slice1, ..., dim1_slice0, ...]
        self._n_dims = len(weights)
        self._n_data = len(data)

    @property
    def payment(self) -> int:
        return self._payment

    @property
    def description(self) -> str:
        return f"gradient_estimation_{self._n_dims}dims_{self._n_data}samples"

    def build_chunks(self) -> list[bytes]:
        """
        Build one chunk per (weight_dimension, data_slice) pair.
        Total chunks = n_weights × n_data_points.
        """
        from unbound.compiler.compiler import compile_source
        from unbound.uvm.encoding import encode

        W_fp = [int(w * SCALE) for w in self._weights]
        eps_fp = int(self._epsilon * SCALE)

        chunks = []
        for dim in range(self._n_dims):
            W_perturbed = W_fp.copy()
            W_perturbed[dim] += eps_fp
            for x, y in self._data:
                src = self._make_source(W_perturbed, x, y)
                stream, _ = compile_source(src)
                chunks.append(encode(stream))

        return chunks

    def _make_source(self, weights_fp: list[int], x: float, y: float) -> str:
        lines = [f"w{i} = {w}" for i, w in enumerate(weights_fp)]
        lines.append(f"x = {int(x * SCALE)}")
        lines.append(f"y = {int(y * SCALE)}")
        lines.append(f"SCALE = {SCALE}")
        lines.append(self._loss_body)
        return "\n".join(lines)

    def aggregate(self, results: list[int]) -> list[float]:
        """
        Convert raw chunk results to a gradient vector.

        results: flat list of loss values (fixed-point), one per chunk
                 ordered as [dim0_slice0, dim0_slice1, ..., dimN_sliceM]
        returns: gradient vector [grad_0, grad_1, ..., grad_N] as floats
        """
        # Compute baseline loss (mean over all data at current weights)
        W_fp = [int(w * SCALE) for w in self._weights]
        baseline = self._compute_baseline()

        gradient = []
        for dim in range(self._n_dims):
            start = dim * self._n_data
            dim_losses = results[start: start + self._n_data]
            mean_loss = sum(dim_losses) / len(dim_losses) / SCALE
            grad_i = (mean_loss - baseline) / self._epsilon
            gradient.append(grad_i)

        return gradient

    def _compute_baseline(self) -> float:
        """Compute mean loss at current weights locally (cheap verification)."""
        from unbound.compiler.compiler import compile_source
        from unbound.uvm.vm import UVM

        W_fp = [int(w * SCALE) for w in self._weights]
        vm = UVM()
        losses = []
        for x, y in self._data:
            src = self._make_source(W_fp, x, y)
            stream, _ = compile_source(src)
            result = vm.execute(stream)
            if result:
                losses.append(result[0] / SCALE)
        return sum(losses) / len(losses) if losses else 0.0


class HyperparamSearch:
    """
    Evaluate many hyperparameter configurations in parallel.

    Each configuration becomes one chunk. Miners train (or simulate training)
    under that configuration and return a validation score. The pool returns
    scores for all configurations — full Bayesian-style search at the cost of
    parallel evaluation.

    Usage
    -----
    job = HyperparamSearch(
        train_body = TRAIN_AND_EVAL,   # Python source using: lr, epochs, reg, SCALE
        configs    = [
            {"lr": 100, "epochs": 10, "reg": 1},     # fixed-point: lr=0.1, reg=0.001
            {"lr": 10,  "epochs": 20, "reg": 10},
            {"lr": 500, "epochs": 5,  "reg": 100},
        ],
        payment    = 150,
    )
    chunks  = job.build_chunks()
    job_id  = client.submit(chunks, payment=job.payment)
    raw     = client.wait(job_id)
    best_config, best_score = job.aggregate(raw)

    Conversion pattern
    ------------------
    Sequential:  best = min(configs, key=lambda c: train_and_eval(c))
    Search:      each config is a candidate; miner trains and evaluates one config
    """

    def __init__(
        self,
        train_body: str,
        configs: list[dict[str, int]],
        payment: int,
        minimize: bool = True,
    ):
        """
        train_body : Python source using config variable names + SCALE
                     ends with print(validation_score_fixed_point)
        configs    : list of dicts mapping variable names to fixed-point ints
        payment    : UBD to lock in escrow
        minimize   : if True, return config with lowest score; else highest
        """
        self._train_body = train_body
        self._configs = configs
        self._payment = payment
        self._minimize = minimize

    @property
    def payment(self) -> int:
        return self._payment

    @property
    def description(self) -> str:
        return f"hyperparam_search_{len(self._configs)}_configs"

    def build_chunks(self) -> list[bytes]:
        from unbound.compiler.compiler import compile_source
        from unbound.uvm.encoding import encode

        chunks = []
        for config in self._configs:
            src = self._make_source(config)
            stream, _ = compile_source(src)
            chunks.append(encode(stream))
        return chunks

    def _make_source(self, config: dict[str, int]) -> str:
        lines = [f"{k} = {v}" for k, v in config.items()]
        lines.append(f"SCALE = {SCALE}")
        lines.append(self._train_body)
        return "\n".join(lines)

    def aggregate(self, results: list[int]) -> tuple[dict, float]:
        """Return (best_config, best_score_as_float)."""
        if self._minimize:
            best_idx = min(range(len(results)), key=lambda i: results[i])
        else:
            best_idx = max(range(len(results)), key=lambda i: results[i])
        return (self._configs[best_idx], results[best_idx] / SCALE)
