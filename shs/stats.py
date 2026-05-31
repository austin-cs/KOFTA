"""Pure statistics for the SHS evaluation protocol (Klees et al.).

No scipy dependency: the Mann-Whitney U p-value uses the tie-corrected normal
approximation with a continuity correction, and the Vargha-Delaney A12 uses the
exact count-based estimator. Both are standard for fuzzing evaluations with
R >= 10 runs. The normal approximation is asymptotic; for very small samples
(R < 5) prefer an exact test. We surface that caveat rather than hide it.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Sequence


def median(xs: Sequence[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty sample")
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0


def _norm_sf(z: float) -> float:
    """Upper-tail survival function 1 - Phi(z) of the standard normal."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def _average_ranks(values: Sequence[float]) -> tuple[list[float], list[int]]:
    """Return average ranks (1-based) for `values` and the tie-group sizes."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    tie_sizes: list[int] = []
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        # positions i..j (0-based) share rank == average of (i+1 .. j+1)
        avg = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        tie_sizes.append(j - i + 1)
        i = j + 1
    return ranks, tie_sizes


def mannwhitney_u(a: Sequence[float], b: Sequence[float]) -> dict:
    """Two-sided Mann-Whitney U test of samples `a` vs `b`.

    Returns {u, u_a, u_b, p, z, n_a, n_b, approximate}. `u` is min(u_a, u_b).
    """
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        raise ValueError("Mann-Whitney U requires non-empty samples")

    combined = list(a) + list(b)
    ranks, tie_sizes = _average_ranks(combined)
    r_a = sum(ranks[:n_a])
    u_a = r_a - n_a * (n_a + 1) / 2.0
    u_b = n_a * n_b - u_a
    u = min(u_a, u_b)

    n = n_a + n_b
    mu = n_a * n_b / 2.0
    tie_term = sum(t**3 - t for t in tie_sizes)
    var = (n_a * n_b / 12.0) * ((n + 1) - tie_term / (n * (n - 1)))

    if var <= 0:
        # All values identical: no evidence of a difference.
        return {"u": u, "u_a": u_a, "u_b": u_b, "p": 1.0, "z": 0.0,
                "n_a": n_a, "n_b": n_b, "approximate": True}

    # Continuity correction toward the mean.
    diff = abs(u_a - mu)
    z = (diff - 0.5) / math.sqrt(var)
    if z < 0:
        z = 0.0
    p = 2.0 * _norm_sf(z)
    p = min(1.0, p)
    return {"u": u, "u_a": u_a, "u_b": u_b, "p": p, "z": z,
            "n_a": n_a, "n_b": n_b, "approximate": True}


def vargha_delaney_a12(a: Sequence[float], b: Sequence[float]) -> float:
    """Vargha-Delaney A12: P(a > b) + 0.5 * P(a == b).

    A12 = 0.5 means stochastic equivalence; A12 > 0.5 means `a` tends to be
    larger than `b`. Exact count-based estimator (no rank approximation).
    """
    n_a, n_b = len(a), len(b)
    if n_a == 0 or n_b == 0:
        raise ValueError("A12 requires non-empty samples")
    # Rank-sum identity: A12 = (R_a/n_a - (n_a+1)/2) / n_b, exact with ties.
    ranks, _ = _average_ranks(list(a) + list(b))
    r_a = sum(ranks[:n_a])
    return (r_a / n_a - (n_a + 1) / 2.0) / n_b


def a12_magnitude(a12: float) -> str:
    """Vargha-Delaney effect-size bands (negligible/small/medium/large).

    Canonical cutoffs on |A12 - 0.5|: 0.06 (small), 0.14 (medium), 0.21 (large),
    i.e. A12 >= 0.71 is large. A small epsilon absorbs float boundary noise so
    that exactly 0.71 / 0.29 land in "large".
    """
    eps = 1e-9
    d = abs(a12 - 0.5)
    if d >= 0.21 - eps:
        return "large"
    if d >= 0.14 - eps:
        return "medium"
    if d >= 0.06 - eps:
        return "small"
    return "negligible"


def compare(treatment: Sequence[float], baseline: Sequence[float]) -> dict:
    """Full comparison of `treatment` vs `baseline` for one (tool x target) cell."""
    mw = mannwhitney_u(treatment, baseline)
    a12 = vargha_delaney_a12(treatment, baseline)
    return {
        "median_treatment": median(treatment),
        "median_baseline": median(baseline),
        "p": mw["p"],
        "z": mw["z"],
        "u": mw["u"],
        "a12": a12,
        "a12_magnitude": a12_magnitude(a12),
        "n_treatment": len(treatment),
        "n_baseline": len(baseline),
    }
