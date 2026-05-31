"""Read real campaign artifacts. Missing artifacts yield None, never a number.

Expected campaign layout (auto-discovered by shs.tables):

  <root>/cov/<target>/<config>/<run>/        AFL state dir (plot_data, fuzzer_stats)
  <root>/cov/<target>/<config>/<run>/edges.txt   optional: afl-showmap edge count
  <root>/magic/<config>/<run>/magic.json     {"string, 2 B": {"solved":1,"planted":1}, ...}
  <root>/undoc/<target>/<config>/<run>/opts.csv  output of kofta-opts -c
  <root>/undoc/documented.json               {"objdump": 78, ...}
  <root>/cost/<target>/<run>/shs_cost.json   SHS service cost record

config names: weifuzz, llmonly, kofta, kshs, kshsng
"""

from __future__ import annotations

import csv
import json
from pathlib import Path


# ---- AFL state-dir readers -------------------------------------------------

def read_plot_data_final(run_dir: Path) -> int | None:
    """Final `paths_total` from AFL plot_data (queue size; a coverage proxy)."""
    p = run_dir / "plot_data"
    if not p.is_file():
        return None
    last = None
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            last = row
    if last is None:
        return None
    for key in (" paths_total", "paths_total"):
        if key in last:
            return int(last[key])
    return None


def read_fuzzer_stats(run_dir: Path) -> dict[str, str] | None:
    p = run_dir / "fuzzer_stats"
    if not p.is_file():
        return None
    out: dict[str, str] = {}
    with p.open() as f:
        for line in f:
            if ":" in line:
                k, _, v = line.partition(":")
                out[k.strip()] = v.strip()
    return out or None


def read_edges(run_dir: Path) -> int | None:
    """Distinct edges from a sidecar `edges.txt` (one integer).

    The campaign runner produces this by replaying the final queue through
    afl-showmap and counting tuples. This is the honest "edge coverage" number;
    prefer it over the plot_data path count.
    """
    p = run_dir / "edges.txt"
    if not p.is_file():
        return None
    txt = p.read_text().strip()
    return int(txt) if txt else None


def run_final_coverage(run_dir: Path, metric: str = "edges") -> int | None:
    """Per-run scalar coverage. metric: 'edges' (afl-showmap) or 'paths'."""
    if metric == "edges":
        e = read_edges(run_dir)
        if e is not None:
            return e
        # No edges.txt -> fall back, but the caller should know it changed.
        return read_plot_data_final(run_dir)
    if metric == "paths":
        return read_plot_data_final(run_dir)
    raise ValueError(f"unknown coverage metric: {metric!r}")


# ---- discovery -------------------------------------------------------------

def _sorted_run_dirs(parent: Path) -> list[Path]:
    if not parent.is_dir():
        return []
    return sorted(d for d in parent.iterdir() if d.is_dir())


def discover_cov(root: Path, target: str, config: str, metric: str) -> list[int]:
    """All per-run coverage values for one (target, config). Empty if none."""
    parent = root / "cov" / target / config
    vals: list[int] = []
    for run in _sorted_run_dirs(parent):
        v = run_final_coverage(run, metric)
        if v is not None:
            vals.append(v)
    return vals


# ---- RQ6 magic-value micro-benchmark --------------------------------------

def read_magic(run_dir: Path) -> dict[str, dict[str, int]] | None:
    p = run_dir / "magic.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def discover_magic(root: Path, config: str) -> dict[str, list[int]]:
    """gate_type -> list of per-run solved counts for `config`."""
    parent = root / "magic" / config
    out: dict[str, list[int]] = {}
    for run in _sorted_run_dirs(parent):
        rec = read_magic(run)
        if not rec:
            continue
        for gate, d in rec.items():
            out.setdefault(gate, []).append(int(d.get("solved", 0)))
    return out


def magic_planted(root: Path) -> dict[str, int]:
    """gate_type -> #planted, from any run's magic.json (planted is fixed)."""
    for config in ("kshs", "kofta", "llmonly", "kshsng"):
        parent = root / "magic" / config
        for run in _sorted_run_dirs(parent):
            rec = read_magic(run)
            if rec:
                return {g: int(d.get("planted", 0)) for g, d in rec.items()}
    return {}


# ---- RQ1-extension undocumented optargs -----------------------------------

def count_optargs(run_dir: Path) -> int | None:
    """Distinct (option, value) pairs discovered, from a kofta-opts opts.csv."""
    p = run_dir / "opts.csv"
    if not p.is_file():
        return None
    n = 0
    with p.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("value"):
                n += 1
    return n


def discover_undoc(root: Path, target: str, config: str) -> list[int]:
    parent = root / "undoc" / target / config
    vals: list[int] = []
    for run in _sorted_run_dirs(parent):
        c = count_optargs(run)
        if c is not None:
            vals.append(c)
    return vals


def documented_counts(root: Path) -> dict[str, int]:
    p = root / "undoc" / "documented.json"
    if not p.is_file():
        return {}
    return {k: int(v) for k, v in json.loads(p.read_text()).items()}


# ---- RQ7 SHS cost ----------------------------------------------------------

def read_cost(run_dir: Path) -> dict | None:
    p = run_dir / "shs_cost.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def discover_cost(root: Path, target: str) -> list[dict]:
    parent = root / "cost" / target
    out: list[dict] = []
    for run in _sorted_run_dirs(parent):
        rec = read_cost(run)
        if rec:
            out.append(rec)
    return out
