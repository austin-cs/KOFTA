"""Orchestrator wiring test -- exercises shs.campaign WITHOUT a real fuzzer.

KOFTA's forkserver only runs on native x86_64 + glibc<=2.33, so the real
campaign->artifact step can only be validated in CI (docker/run-pilot.sh). This
test instead substitutes the fuzzer / showmap / kofta-opts commands with tiny
fakes that emit the real artifact shapes, so the orchestration logic itself --
exit-124 (timeout) acceptance, edge counting, and the opts.csv / shs_cost.json
mirrors into the layout the loaders expect -- is checked on any host.

It is a wiring test, not a measurement: every number here is synthetic.

Run: python3 -m shs.tests.test_campaign   (or via pytest)
"""

import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from shs import campaign, loaders, tables


def _script(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _make_spec(root: Path, tools: Path, configs, exit_code: int = 124) -> campaign.Spec:
    """A spec whose commands fabricate AFL-shaped artifacts then exit `exit_code`.

    fakefuzz makes out/queue/<seed>, out/plot_data and writes the {cost} record;
    fakeshowmap emits three tuple lines; fakeopts writes a 2-row opts.csv.
    """
    fakefuzz = _script(tools / "fakefuzz.sh",
        'out="$1"; cost="$2"\n'
        'mkdir -p "$out/queue"\n'
        'echo seed > "$out/queue/id000"\n'
        'printf "unix_time, paths_total\\n0, 7\\n" > "$out/plot_data"\n'
        'cat > "$cost" <<EOF\n'
        '{"llm_calls":3,"cache_hits":10,"tokens":120,"latency_s":[0.4,0.6],"shs_wall_s":5}\n'
        'EOF\n'
        f'exit {exit_code}\n')
    fakeshowmap = _script(tools / "fakeshowmap.sh",
        'output="$1"\n'
        'printf "000001:1\\n000002:1\\n000003:1\\n" > "$output"\n')
    fakeopts = _script(tools / "fakeopts.sh",
        'out="$1"\n'
        'printf "option,value,count\\n--foo,bar,1\\n--baz,qux,2\\n" > "$out/opts.csv"\n')

    cmd = f"sh {fakefuzz} {{out}} {{cost}}"
    return campaign.Spec(
        root=root,
        targets=["smoke"],
        runs=2,
        duration_s=1,
        commands={c: cmd for c in configs},
        showmap=f"sh {fakeshowmap} {{output}} {{input}}",
        opts_cmd=f"sh {fakeopts} {{out}}",
        env={},
    )


def test_timeout_exit_124_is_success_and_postprocess_runs():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "campaign"
        tools = Path(d) / "tools"
        tools.mkdir()
        spec = _make_spec(root, tools, ["kofta"], exit_code=124)

        ok = campaign.launch_one(spec, "kofta", "smoke", 0, dry=False)
        assert ok, "exit 124 (timeout) must count as a successful full-budget run"

        run_dir = root / "cov" / "smoke" / "kofta" / "run-00"
        assert (run_dir / ".shs_done").is_file(), "done marker not written"
        # count_edges replayed the queue through fakeshowmap -> 3 distinct tuples.
        assert loaders.read_edges(run_dir) == 3, "edges.txt wrong/missing"
        # opts.csv mirrored into undoc/<target>/<config>/<run>/ (the loader path).
        undoc = root / "undoc" / "smoke" / "kofta" / "run-00"
        assert loaders.count_optargs(undoc) == 2, "opts.csv not mirrored to undoc/"
        # cost record mirrored into cost/<target>/<run>/ with wall patched in.
        cost = loaders.read_cost(root / "cost" / "smoke" / "run-00")
        assert cost and "campaign_wall_s" in cost, "cost not mirrored/patched"


def test_real_failure_returns_false():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "campaign"
        tools = Path(d) / "tools"
        tools.mkdir()
        spec = _make_spec(root, tools, ["kofta"], exit_code=1)
        ok = campaign.launch_one(spec, "kofta", "smoke", 0, dry=False)
        assert not ok, "a genuine non-zero/non-124 exit must be reported as failure"
        assert not (root / "cov" / "smoke" / "kofta" / "run-00" / ".shs_done").is_file()


def test_full_pilot_roundtrip_renders_real_rows():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "campaign"
        tools = Path(d) / "tools"
        tools.mkdir()
        spec = _make_spec(root, tools, ["kofta", "kshs"])
        campaign.run_campaign(spec, dry=False)

        # The loaders see 2 runs/config for the pilot target.
        assert loaders.discover_cov(root, "smoke", "kofta", "edges") == [3, 3]
        assert loaders.discover_cov(root, "smoke", "kshs", "edges") == [3, 3]

        # kofta-stats --targets smoke equivalent: the smoke cov row is populated.
        rows, facts = tables.table_cov(root, "edges", targets=["smoke"])
        smoke = [r for r in rows if r.startswith("smoke")][0]
        assert tables.PLACEHOLDER not in smoke.split("&")[4], smoke  # kshs cell
        assert facts["n_targets"] == 1, facts

        # cost table for the pilot target renders a numeric calls/h, not [\;].
        crows, _ = tables.table_cost(root, targets=["smoke"])
        smoke_cost = [r for r in crows if r.startswith("smoke")][0]
        assert tables.PLACEHOLDER not in smoke_cost, smoke_cost


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
    print("\nall campaign tests passed")


if __name__ == "__main__":
    main()
