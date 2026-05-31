"""End-to-end check of the table emitter on a synthetic campaign tree.

Builds a tiny fake campaign with real artifact formats (plot_data, edges.txt,
magic.json, opts.csv, shs_cost.json), runs the emitters, and asserts that
populated cells render numbers and unpopulated cells stay as the [\\;]
placeholder. Run: python3 -m shs.tests.test_tables
"""

import json
import sys
import tempfile
from pathlib import Path

from shs import tables


def _write_edges(run: Path, n: int):
    run.mkdir(parents=True, exist_ok=True)
    (run / "edges.txt").write_text(str(n))


def _build(root: Path):
    # cov: objdump fully populated across 4 configs x 10 runs; others empty.
    base = {"weifuzz": 1000, "llmonly": 900, "kofta": 1500, "kshs": 1800}
    for cfg, center in base.items():
        for i in range(10):
            _write_edges(root / "cov" / "objdump" / cfg / f"run-{i:02d}",
                        center + i * 3)
    # magic micro-benchmark
    for cfg, solved in {"llmonly": 1, "kofta": 1, "kshs": 4, "kshsng": 2}.items():
        run = root / "magic" / cfg / "run-00"
        run.mkdir(parents=True, exist_ok=True)
        rec = {
            "string, 2\\,B": {"solved": 1 if solved >= 1 else 0, "planted": 1},
            "string, 4\\,B": {"solved": 1 if cfg == "kshs" else 0, "planted": 1},
            "string, 8\\,B": {"solved": 1 if cfg == "kshs" else 0, "planted": 1},
            "magic number": {"solved": 1 if solved >= 2 else 0, "planted": 1},
        }
        (run / "magic.json").write_text(json.dumps(rec))
    # undoc
    (root / "undoc").mkdir(parents=True, exist_ok=True)
    (root / "undoc" / "documented.json").write_text(json.dumps({"objdump": 78}))
    for cfg, n in {"llmonly": 5, "kofta": 9, "kshs": 14}.items():
        run = root / "undoc" / "objdump" / cfg / "run-00"
        run.mkdir(parents=True, exist_ok=True)
        lines = ["option,value,count"]
        for k in range(n):
            lines.append(f"--opt{k},val{k},1")
        (run / "opts.csv").write_text("\n".join(lines) + "\n")
    # cost
    run = root / "cost" / "objdump" / "run-00"
    run.mkdir(parents=True, exist_ok=True)
    (run / "shs_cost.json").write_text(json.dumps({
        "llm_calls": 12, "cache_hits": 88, "latency_s": [0.8, 1.2, 1.0],
        "tokens": 4800, "shs_wall_s": 300, "campaign_wall_s": 86400,
    }))


def test_cov_numbers_and_placeholder():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build(root)
        rows, facts = tables.table_cov(root, "edges")
        body = "\n".join(rows)
        assert "objdump" in body
        # objdump populated -> has a numeric A12 like [0.xx] and a +Delta%
        objline = [r for r in rows if r.startswith("objdump")][0]
        assert tables.PLACEHOLDER not in objline, objline
        assert "+78.9\\%" in objline, objline  # filled cells carry no [..] brackets
        # readelf has no data -> all-placeholder row
        readline = [r for r in rows if r.startswith("readelf")][0]
        assert readline.count(tables.PLACEHOLDER) >= 4, readline
        # facts: KSHS (1800+) beats KOFTA (1500+), large effect, significant
        assert facts["n_targets"] == 1
        assert facts["kshs_wins"] == 1
        assert facts["sig_targets"] == 1
        assert facts["large_effect"] == 1


def test_magic_total():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build(root)
        rows, facts = tables.table_magic(root)
        assert facts["by_config"]["kshs"] == 4, facts
        assert facts["by_config"]["kofta"] == 1, facts
        assert facts["total_planted"] == 4


def test_undoc_and_cost_render():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _build(root)
        urows, _ = tables.table_undoc(root)
        oline = [r for r in urows if r.startswith("objdump")][0]
        assert "78" in oline and "14" in oline, oline
        crows, _ = tables.table_cost(root)
        oline = [r for r in crows if r.startswith("objdump")][0]
        # 12 calls over 86400 s -> 0.5/h; hit% = 88/100 = 88
        assert "0.5" in oline and "88" in oline, oline


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
    print("\nall table tests passed")


if __name__ == "__main__":
    main()
