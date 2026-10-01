import math

from mastodon_utils import stats as S


def test_median_and_quantile():
    assert S.median([3, 1, 2]) == 2
    assert S.median([1, 2, 3, 4]) == 2.5
    assert S.median([]) is None
    assert S.quantile([0, 10], 0.9) == 9


def test_hodges_lehmann_resists_outlier():
    xs = [10, 11, 9, 10, 12, 10, 5000]
    assert S.mean(xs) > 700
    assert 9 <= S.hodges_lehmann(xs) <= 12


def test_gini_extremes():
    assert S.gini([5, 5, 5, 5]) == 0
    assert math.isclose(S.gini([0, 0, 0, 10]), 0.75)
    assert S.gini([0, 0]) == 0


def test_h_index():
    assert S.h_index([10, 8, 5, 4, 3]) == 4
    assert S.h_index([0, 0]) == 0
    assert S.h_index([100]) == 1


def test_theil_sen_ignores_outlier():
    xs = list(range(10))
    ys = [2 * x for x in xs]
    ys[5] = 1000
    assert math.isclose(S.theil_sen(xs, ys), 2.0)


def test_effective_number():
    assert math.isclose(S.effective_number([1] * 10), 10)
    assert math.isclose(S.effective_number([10]), 1)
    assert S.effective_number([]) == 0


def test_burstiness():
    assert math.isclose(S.burstiness([5, 5, 5, 5]), -1)
    assert S.burstiness([1, 1, 1, 100]) > 0.2
    assert S.burstiness([1] * 20 + [500, 800]) > 0.5


def test_circular_hour_wraps_midnight():
    mean_h, r = S.circular_hour([23, 1])
    assert min(mean_h, 24 - mean_h) < 1e-6
    assert r > 0.9


def test_smoothing_shrinks_small_samples():
    # 1 of 1 is not "100%" with a prior
    assert S.beta_smooth(1, 1, 0.3, 10) < 0.4
    assert math.isclose(S.beta_smooth(500, 1000, 0.3, 10), 0.498, abs_tol=0.01)


def test_geometric_mean_punishes_weak_dimension():
    strong = S.weighted_geometric_mean({"a": 0.9, "b": 0.9}, {"a": 1, "b": 1})
    lopsided = S.weighted_geometric_mean({"a": 1.0, "b": 0.1}, {"a": 1, "b": 1})
    assert strong > lopsided
