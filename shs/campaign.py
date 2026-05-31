"""Campaign orchestration for the SHS evaluation.

Drives the RQ5-RQ7 matrix: configs x targets x R runs, each for a wall-clock
budget, into the directory layout that shs.loaders / kofta-stats consume. The
exact fuzzer invocation differs per config and per machine, so commands are not
hardcoded -- they come from a campaign spec (see campaign.example.json). The
orchestrator owns the matrix, the layout, resumption, and post-run extraction
(edges via afl-showmap, optargs via kofta-opts, SHS cost record).

Nothing here fabricates data; it only launches real runs and records what they
produce. A dry run prints the plan and the resolved commands without executing.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from debug import pact, pfatal, pok, psay, pwarn

CONFIGS = ["weifuzz", "llmonly", "kofta", "kshs", "kshsng"]


@dataclass
class Spec:
    root: Path
    targets: list[str]
    runs: int
    duration_s: int
    commands: dict[str, str]      # config -> shell template
    showmap: str | None           # afl-showmap template for edge counting
    opts_cmd: str | None          # kofta-opts template for undoc extraction
    env: dict[str, str]

    @classmethod
    def load(cls, path: Path) -> "Spec":
        d = json.loads(path.read_text())
        missing = [c for c in CONFIGS if c not in d.get("commands", {})]
        if missing:
            pwarn(f"spec has no command for: {', '.join(missing)} "
                  f"(those cells will stay [\\;])")
        return cls(
            root=Path(d["root"]).expanduser(),
            targets=list(d["targets"]),
            runs=int(d.get("runs", 10)),
            duration_s=int(d.get("duration_s", 24 * 3600)),
            commands=dict(d.get("commands", {})),
            showmap=d.get("showmap"),
            opts_cmd=d.get("opts_cmd"),
            env=dict(d.get("env", {})),
        )


def _subst(template: str, **kw) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _run_dir(spec: Spec, config: str, target: str, i: int) -> Path:
    return spec.root / "cov" / target / config / f"run-{i:02d}"


def _done_marker(run_dir: Path) -> Path:
    return run_dir / ".shs_done"


def plan(spec: Spec) -> list[tuple[str, str, int]]:
    jobs = []
    for target in spec.targets:
        for config in CONFIGS:
            if config not in spec.commands:
                continue
            for i in range(spec.runs):
                jobs.append((config, target, i))
    return jobs


def launch_one(spec: Spec, config: str, target: str, i: int,
               dry: bool) -> bool:
    run_dir = _run_dir(spec, config, target, i)
    if _done_marker(run_dir).is_file():
        pact(f"skip (done): {config}/{target}/run-{i:02d}")
        return True
    run_dir.mkdir(parents=True, exist_ok=True)

    cmd = _subst(spec.commands[config], target=target, out=run_dir,
                 duration=spec.duration_s, run=i,
                 cache=run_dir / "shs_cache.json",
                 cost=run_dir / "shs_cost.json")
    psay(f"$ {cmd}")
    if dry:
        return True

    env = None
    if spec.env:
        import os
        env = {**os.environ, **spec.env}
    t0 = time.time()
    proc = subprocess.run(cmd, shell=True, env=env)
    wall = time.time() - t0
    # A fuzzer runs until its wall-clock budget; the command templates cap it
    # with `timeout {duration}`, and GNU timeout reports 124 when it had to send
    # the signal (the normal end of a campaign, not an error). AFL itself exits
    # 0 on SIGTERM, but timeout's own status masks that. Treat both as success;
    # anything else is a real failure (build/seed/forkserver problem).
    if proc.returncode not in (0, 124):
        pwarn(f"run exited {proc.returncode}: {config}/{target}/run-{i:02d}")
        return False

    _postprocess(spec, config, target, run_dir, wall)
    _done_marker(run_dir).write_text(str(int(time.time())))
    pok(f"done: {config}/{target}/run-{i:02d} ({wall:.0f}s)")
    return True


def _postprocess(spec: Spec, config: str, target: str, run_dir: Path,
                 wall: float) -> None:
    """Extract edges.txt, opts.csv, and patch campaign_wall_s into the cost record."""
    if spec.showmap:
        edges = count_edges(spec, target, run_dir)
        if edges is not None:
            (run_dir / "edges.txt").write_text(str(edges))
    if spec.opts_cmd:
        cmd = _subst(spec.opts_cmd, target=target, out=run_dir, state=run_dir)
        subprocess.run(cmd, shell=True)
        # kofta-opts writes opts.csv into {out} (= the cov run dir), but the
        # undoc table loader reads <root>/undoc/<target>/<config>/<run>/opts.csv.
        # Mirror it there so tab:undoc is populated (same pattern as cost below).
        src = run_dir / "opts.csv"
        if src.is_file():
            dst = spec.root / "undoc" / target / config / run_dir.name
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "opts.csv").write_text(src.read_text())
    cost = run_dir / "shs_cost.json"
    if cost.is_file():
        try:
            d = json.loads(cost.read_text())
            d["campaign_wall_s"] = wall
            cost.write_text(json.dumps(d, indent=2))
            # mirror into cost/<target>/run for kofta-stats tab:cost
            dst = spec.root / "cost" / target / f"run-{run_dir.name.split('-')[-1]}"
            dst.mkdir(parents=True, exist_ok=True)
            (dst / "shs_cost.json").write_text(json.dumps(d, indent=2))
        except json.JSONDecodeError:
            pass


def count_edges(spec: Spec, target: str, run_dir: Path) -> int | None:
    """Union of edge tuples over the final queue via afl-showmap.

    Per-file showmap, accumulating distinct tuple IDs. Slower than a collective
    pass but works on stock afl-showmap and is a one-time post-run cost.
    """
    queue = run_dir / "queue"
    if not queue.is_dir():
        pwarn(f"no queue dir in {run_dir}; cannot count edges")
        return None
    seen: set[int] = set()
    tmp = run_dir / ".showmap.out"
    files = [f for f in sorted(queue.iterdir()) if f.is_file()]
    for f in files:
        cmd = _subst(spec.showmap, target=target, input=f, output=tmp)
        r = subprocess.run(cmd, shell=True, capture_output=True)
        if r.returncode not in (0, 1, 2):  # showmap uses exit code for crash class
            continue
        if tmp.is_file():
            for line in tmp.read_text().splitlines():
                line = line.split(":")[0].strip()
                if line.isdigit():
                    seen.add(int(line))
    if tmp.is_file():
        tmp.unlink()
    return len(seen)


def run_campaign(spec: Spec, dry: bool) -> None:
    jobs = plan(spec)
    total_h = len(jobs) * spec.duration_s / 3600.0
    pok(f"{len(jobs)} runs planned "
        f"({len(spec.targets)} targets x {len([c for c in CONFIGS if c in spec.commands])} configs "
        f"x {spec.runs} runs); ~{total_h:.0f} core-hours of fuzzing")
    if total_h > 1000 and not dry:
        pwarn(f"this is {total_h:.0f} core-hours; consider --dry-run first or "
              f"parallelizing across machines by target/config")
    ok = bad = 0
    for (config, target, i) in jobs:
        if launch_one(spec, config, target, i, dry):
            ok += 1
        else:
            bad += 1
    pok(f"campaign finished: {ok} ok, {bad} failed/skipped")
    if not dry:
        psay("")
        pok("now generate tables:  ./kofta-stats " + str(spec.root))
