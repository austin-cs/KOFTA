"""Self-checks for shs.stats. Run: python3 -m shs.tests.test_stats"""

import sys

from shs import stats


def approx(a, b, tol=1e-6):
    return abs(a - b) <= tol


def test_median():
    assert stats.median([1, 2, 3]) == 2
    assert stats.median([1, 2, 3, 4]) == 2.5
    assert stats.median([5]) == 5


def test_a12_known():
    # Perfect separation: every a beats every b -> A12 = 1.0
    assert approx(stats.vargha_delaney_a12([4, 5, 6], [1, 2, 3]), 1.0)
    # Reverse -> 0.0
    assert approx(stats.vargha_delaney_a12([1, 2, 3], [4, 5, 6]), 0.0)
    # Identical distributions -> 0.5
    assert approx(stats.vargha_delaney_a12([1, 2, 3], [1, 2, 3]), 0.5)
    # One tie pair: a=[1,2], b=[2,3]; pairs: (1,2)<,(1,3)<,(2,2)=,(2,3)< ->
    # (#a>b=0 + 0.5*#eq=0.5)/4 = 0.125
    assert approx(stats.vargha_delaney_a12([1, 2], [2, 3]), 0.125)


def test_a12_magnitude():
    assert stats.a12_magnitude(0.5) == "negligible"
    assert stats.a12_magnitude(0.71) == "large"
    assert stats.a12_magnitude(0.29) == "large"
    assert stats.a12_magnitude(0.60) == "small"


def test_mwu_separation():
    # Fully separated samples -> small p, U == 0
    r = stats.mannwhitney_u([10, 11, 12, 13, 14, 15, 16, 17, 18, 19],
                            [0, 1, 2, 3, 4, 5, 6, 7, 8, 9])
    assert r["u"] == 0.0
    assert r["p"] < 0.01, r["p"]


def test_mwu_identical():
    r = stats.mannwhitney_u([1, 2, 3], [1, 2, 3])
    assert approx(r["p"], 1.0)


def test_mwu_reference_value():
    # Reference: a=[1,2,3,4], b=[5,6,7,8]; U_a = 0, U_b = 16, U = 0.
    r = stats.mannwhitney_u([1, 2, 3, 4], [5, 6, 7, 8])
    assert approx(r["u_a"], 0.0)
    assert approx(r["u_b"], 16.0)


def main():
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok   {name}")
            except AssertionError as e:
                failures += 1
                print(f"  FAIL {name}: {e}")
    if failures:
        print(f"\n{failures} test(s) failed")
        sys.exit(1)
    print("\nall stats tests passed")


if __name__ == "__main__":
    main()
