"""Self-checks for shs.service. Run: python3 -m shs.tests.test_service"""

import sys
import tempfile
from pathlib import Path

from shs import service
from shs.service import (BranchRecord, Budget, Cache, Cost, MockClient,
                         SHSService, build_prompt, parse_candidates)


REC = BranchRecord(
    option="--format", sink_type="strcmp",
    source_slice='if (!strcmp(arg, "png")) ...\nelse if (!strcmp(arg, "tiff")) ...',
)


def test_cache_key_stable_and_specific():
    a = REC.cache_key()
    b = BranchRecord("--format", "strcmp", REC.source_slice).cache_key()
    assert a == b  # same option + slice -> same key
    c = BranchRecord("--mode", "strcmp", REC.source_slice).cache_key()
    assert a != c  # different option -> different key


def test_prompt_contains_facts():
    p = build_prompt(REC, k=8)
    assert "--format" in p and "strcmp" in p and "png" in p
    assert "at most 8 items" in p


def test_parse_candidates():
    assert parse_candidates('["png","tiff","jpeg"]', 8) == ["png", "tiff", "jpeg"]
    assert parse_candidates('here you go: ["a", "b"] ok', 8) == ["a", "b"]
    assert parse_candidates('["a","a","b"]', 8) == ["a", "b"]  # dedup
    assert parse_candidates('["a","b","c"]', 2) == ["a", "b"]  # k cap
    assert parse_candidates("not json at all", 8) == []  # never raises


def test_cache_hit_is_free():
    with tempfile.TemporaryDirectory() as d:
        svc = SHSService(MockClient(), Cache(Path(d) / "c.json"),
                         Budget(per_hour=100), k=8)
        first = svc.query(REC)
        assert first == ["png", "tiff"], first
        assert svc.cost.llm_calls == 1 and svc.cost.cache_hits == 0
        second = svc.query(REC)
        assert second == first
        assert svc.cost.llm_calls == 1 and svc.cost.cache_hits == 1  # no new call


def test_cache_persists_across_instances():
    with tempfile.TemporaryDirectory() as d:
        cpath = Path(d) / "c.json"
        SHSService(MockClient(), Cache(cpath), Budget(100)).query(REC)
        svc2 = SHSService(MockClient(), Cache(cpath), Budget(100))
        svc2.query(REC)
        assert svc2.cost.cache_hits == 1 and svc2.cost.llm_calls == 0


def test_budget_degrades_gracefully():
    with tempfile.TemporaryDirectory() as d:
        svc = SHSService(MockClient(), Cache(Path(d) / "c.json"), Budget(0))
        assert svc.query(REC) == []  # budget 0 -> SHS disabled, KOFTA falls back
        assert svc.cost.budget_skips == 1 and svc.cost.llm_calls == 0


def test_budget_sliding_window():
    b = Budget(per_hour=2)
    assert b.allow(now=0.0); b.charge(now=0.0)
    assert b.allow(now=1.0); b.charge(now=1.0)
    assert not b.allow(now=2.0)             # 2 calls in the last hour
    assert b.allow(now=3601.0)              # first call aged out of the window


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
    print("\nall service tests passed")


if __name__ == "__main__":
    main()
