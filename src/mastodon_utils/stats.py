"""Statistical building blocks. Pure functions, no I/O.

Chosen for robustness on small, heavy-tailed samples: social engagement is
dominated by a few viral posts, so plain means and OLS mislead.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable, Sequence


def median(xs: Sequence[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def quantile(xs: Sequence[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def mean(xs: Sequence[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def hodges_lehmann(xs: Sequence[float]) -> float | None:
    """Median of all pairwise (Walsh) averages.

    Robust like the median (29% breakdown point) yet ~95% as efficient as the
    mean on well-behaved data, so one viral post can't define "typical".
    """
    n = len(xs)
    if n == 0:
        return None
    if n > 800:  # O(n^2); fall back to a 10% trimmed mean for very large samples
        s = sorted(xs)
        k = n // 10
        return mean(s[k : n - k])
    walsh = [(xs[i] + xs[j]) / 2.0 for i in range(n) for j in range(i, n)]
    return median(walsh)


def gini(xs: Sequence[float]) -> float | None:
    """0 = every post gets the same engagement, 1 = one post gets everything."""
    n = len(xs)
    if n == 0:
        return None
    s = sorted(max(0.0, x) for x in xs)
    total = sum(s)
    if total == 0:
        return 0.0
    cum = sum((i + 1) * x for i, x in enumerate(s))
    return (2.0 * cum) / (n * total) - (n + 1.0) / n


def top_share(xs: Sequence[float], fraction: float = 0.1) -> float | None:
    """Share of the total contributed by the top `fraction` of items."""
    if not xs:
        return None
    total = sum(xs)
    if total == 0:
        return 0.0
    k = max(1, math.ceil(len(xs) * fraction))
    return sum(sorted(xs, reverse=True)[:k]) / total


def h_index(xs: Iterable[float]) -> int:
    """Largest h such that h posts each have at least h interactions."""
    s = sorted(xs, reverse=True)
    h = 0
    for i, x in enumerate(s, start=1):
        if x >= i:
            h = i
        else:
            break
    return h


def theil_sen(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Median of pairwise slopes: a trend line that ignores outliers."""
    n = len(xs)
    if n < 3:
        return None
    slopes = []
    step = max(1, n // 400)  # keep pairs manageable on huge samples
    idx = list(range(0, n, step))
    for a in range(len(idx)):
        for b in range(a + 1, len(idx)):
            i, j = idx[a], idx[b]
            dx = xs[j] - xs[i]
            if dx != 0:
                slopes.append((ys[j] - ys[i]) / dx)
    return median(slopes)


def shannon_entropy(counts: Iterable[int]) -> float:
    c = [x for x in counts if x > 0]
    total = sum(c)
    if total == 0:
        return 0.0
    return -sum((x / total) * math.log(x / total) for x in c)


def effective_number(counts: Iterable[int]) -> float:
    """Hill number of order 1: exp(entropy).

    "How many equally-weighted partners would give this much diversity?"
    10 replies all to one person -> 1.0; 10 replies to 10 people -> 10.0.
    """
    c = list(counts)
    if not any(c):
        return 0.0
    return math.exp(shannon_entropy(c))


def beta_smooth(successes: float, trials: float, prior_mean: float, strength: float) -> float:
    """Posterior mean with a Beta prior: shrinks small-sample rates toward a sensible baseline."""
    return (successes + prior_mean * strength) / (trials + strength)


def dirichlet_smooth(counts: dict[str, float], prior: dict[str, float], strength: float) -> dict[str, float]:
    total = sum(counts.values())
    return {k: (counts.get(k, 0) + prior[k] * strength) / (total + strength) for k in prior}


def burstiness(gaps: Sequence[float]) -> float | None:
    """Goh-Barabasi burstiness of inter-event gaps: (sd - mean) / (sd + mean).

    -1 = perfectly regular (clockwork), 0 = random (Poisson), toward +1 = bursty.
    """
    if len(gaps) < 2:
        return None
    m = mean(gaps) or 0.0
    sd = math.sqrt(sum((g - m) ** 2 for g in gaps) / len(gaps))
    if sd + m == 0:
        return None
    return (sd - m) / (sd + m)


def circular_hour(hours: Sequence[float]) -> tuple[float | None, float | None]:
    """Circular mean of times of day, plus concentration R (0 = spread all day, 1 = same minute).

    A plain average of 23:00 and 01:00 is noon; the circular mean is midnight.
    """
    if not hours:
        return None, None
    sx = sum(math.cos(2 * math.pi * h / 24) for h in hours)
    sy = sum(math.sin(2 * math.pi * h / 24) for h in hours)
    n = len(hours)
    r = math.hypot(sx, sy) / n
    if r < 1e-9:
        return None, 0.0
    angle = math.atan2(sy, sx)
    return (angle * 24 / (2 * math.pi)) % 24, r


def saturate(x: float, scale: float) -> float:
    """Map [0, inf) onto [0, 1): 1 - exp(-x/scale). x = scale gives 0.63."""
    return 1.0 - math.exp(-max(0.0, x) / scale)


def logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def weighted_geometric_mean(values: dict[str, float], weights: dict[str, float], floor: float = 0.01) -> float:
    """Composite that punishes a weak dimension: you can't buy a high score with one strength."""
    total_w = sum(weights.values())
    return math.exp(sum(weights[k] * math.log(max(floor, min(1.0, values[k]))) for k in weights) / total_w)


def top_counts(counter: Counter, n: int = 5) -> list[list]:
    return [[k, v] for k, v in counter.most_common(n)]
