# KOFTA

**KOFTA** is a command-line **option / argument** fuzzer built on top of
[american fuzzy lop](#built-on-afl). Where stock AFL mutates a program's *input
file*, KOFTA also mutates the program's *argv* — the options and option
arguments a CLI tool accepts — using taint inference to discover which arguments
actually steer execution. It implements a multi-argument forkserver that swaps
`argc`/`argv` between executions without restarting the target, so option
exploration stays cheap.

On top of that, KOFTA adds **Semantic Hint Synthesis (SHS)**: when byte-level
mutation gets stuck on a discrete magic-value gate (e.g. `strcmp(arg, "tiff")`),
KOFTA queries an LLM for candidate argument values and validates each candidate
in the forkserver. A bad guess costs exactly one fork, so hallucinations are
harmless. SHS is optional, budgeted, cached, and degrades gracefully to plain
taint inference when its budget is exhausted.

The research write-up (the KOFTA / SHS paper) is maintained separately while it
is under review and will be released here upon publication.

---

## Requirements

KOFTA's runtime contains x86-64-specific assembly (the `argv` memory-leak trick
that powers the multi-argument forkserver) and its LLVM instrumentation pass
uses the LLVM 12 legacy pass manager. As a result the build/run environment is
pinned:

| Requirement | Why |
|---|---|
| **native x86-64 Linux** | the forkserver's `argv`-leak relies on the x86-64 stack layout |
| **glibc ≤ 2.33** (Ubuntu 20.04) | the hardcoded `argv` stack offset breaks on glibc 2.34+ (Ubuntu 22.04) |
| **clang/clang++ 12, llvm-12** | the `llvm_mode` pass targets the LLVM 12 legacy API |
| Python 3.8+ | the `kofta-*` helper scripts |

> Emulated x86-64 (e.g. QEMU on Apple Silicon) running an Ubuntu 20.04 image
> works for development and CI; it is just slower than native. The hard
> requirement is the *glibc version*, not native CPU.

The SHS scripts additionally need the `anthropic` Python SDK and an
`ANTHROPIC_API_KEY` **only** for live LLM queries. Every SHS path has an offline
`--mock` mode that needs neither.

## Build

```shell
# core tools (afl-fuzz, afl-showmap, afl-tmin, ...). AFL_NO_X86 skips the
# legacy gcc-mode x86 self-test we don't use.
$ AFL_NO_X86=1 make CC=clang-12

# LLVM instrumentation (afl-clang-fast / afl-clang-fast++)
$ make -C llvm_mode LLVM_CONFIG=llvm-config-12 CC=clang-12 CXX=clang++-12
```

Set `KOFTA_DEBUG=1` on the core `make` to build with the verbose SHS/taint trace
log enabled.

## Quick start

```shell
# 1. Instrument the target with afl-clang-fast. The LLVM pass also writes:
#      - the discovered option list   -> $KOFTA_OPTSAVE
#      - a branch source-slice map    -> $KOFTA_SRCMAP   (used by SHS)
$ KOFTA_OPTSAVE=opts/target.txt KOFTA_SRCMAP=srcmap.txt \
  AFL_CC=clang-12 ./afl-clang-fast -g target.c -o bin/target

# 2. Fuzz it. -k feeds the discovered option list into argv mutation.
$ ./afl-fuzz -i seeds/ -o out/ -m none -k opts/target.txt -- bin/target

# 3. (optional) collect the options KOFTA actually exercised
$ ./kofta-opts out/ -c          # writes opts.csv
```

To enable SHS, set the `KOFTA_SHS*` environment variables (see below) before
`afl-fuzz`.

## Toolset

| Tool | Purpose |
|---|---|
| `afl-fuzz` | the fuzzer; KOFTA's multi-argument forkserver + taint inference live here |
| `afl-clang-fast` / `afl-clang-fast++` | compile-time instrumentation (LLVM 12); emits the option list + branch srcmap |
| `afl-showmap` | replay one input and dump its edge coverage (used to score campaigns) |
| `kofta-opts` | parse an AFL state dir's `arglist/` and tabulate the options/values explored (`-c` CSV, `-x` Wei-fuzz `parameters.xml`) |
| `kofta-shs` | the SHS query endpoint: branch record in (JSON), ranked candidate values out |
| `kofta-campaign` | run the RQ5–RQ7 evaluation matrix (configs × targets × runs) from a JSON spec |
| `kofta-stats` | read campaign artifacts and emit the paper's LaTeX table rows + prose facts |
| `kofta-plot` | coverage-over-time plots from `plot_data` |
| `kofta-triage` | crash triage helper |

## Semantic Hint Synthesis (SHS)

When a branch is *stuck* on a magic-value comparison that byte mutation cannot
crack, `afl-fuzz.c` hands the branch (its option name, sink type, and the source
slice recorded by the LLVM pass) to `kofta-shs`, which returns ranked candidate
strings. Each candidate is injected into the hint pool and validated by a single
fork — so SHS can only help, never regress correctness.

`kofta-shs` runs in two modes:

```shell
# one record on stdin -> JSON array of candidates on stdout
$ echo '{"option":"--format","sink_type":"strcmp",
         "source_slice":"if(!strcmp(arg,\"png\")) ... else if(!strcmp(arg,\"tiff\")) ..."}' \
    | ./kofta-shs query --model claude-haiku-4-5-20251001 --cache shs_cache.json

# long-lived co-process: newline-delimited JSON requests (what afl-fuzz uses)
$ ./kofta-shs serve --mock        # offline, deterministic, no API / no cost
```

`afl-fuzz` is wired to SHS through environment variables:

| Variable | Meaning |
|---|---|
| `KOFTA_SHS=1` | enable the SHS seam |
| `KOFTA_SHS_BIN` | path to the `kofta-shs` script |
| `KOFTA_SHS_BUDGET` | max real LLM calls per hour (`0` disables SHS entirely) |
| `KOFTA_SHS_MODEL` | chat-completion model id (omit for mock) |
| `KOFTA_SHS_CACHE` | persistent prompt→candidates cache file |
| `KOFTA_SHS_COST` | write a `shs_cost.json` cost record here on exit |
| `KOFTA_SHS_NOSLICE=1` | ablation: send the option name only, no source slice |
| `KOFTA_SRCMAP` | branch source-slice map written at instrumentation time |

Two mechanisms keep LLM usage bounded: a **prompt cache** keyed by
`source_slice + option` (each branch is queried at most once per campaign), and
a **per-hour call budget** after which SHS silently falls back to pure
byte-level taint inference.

## Evaluation pipeline

The paper compares five configurations — `weifuzz`, `llmonly`, `kofta`, `kshs`
(full SHS), and `kshsng` (SHS without the source slice) — over a set of targets
and runs, then reports edge coverage (RQ5), magic-value penetration (RQ6), and
SHS cost (RQ7).

`kofta-campaign` drives the matrix from a JSON spec
([`shs/campaign.example.json`](shs/campaign.example.json) is an annotated
template). Each config is a command template with `{target} {out} {duration}
{run} {cache} {cost}` substituted in. Completed runs are marked `.shs_done` and
skipped on re-run, so an interrupted campaign resumes.

```shell
$ ./kofta-campaign shs/campaign.example.json --dry-run        # print the plan
$ ./kofta-campaign my_campaign.json --only kshs,kofta         # config subset
$ ./kofta-campaign my_campaign.json --targets objdump         # target subset
```

Post-processing extracts artifacts into the layout `kofta-stats` expects:

```
<root>/cov/<target>/<config>/<run>/     AFL state dir + edges.txt (afl-showmap)
<root>/undoc/<target>/<config>/<run>/   opts.csv (from kofta-opts)
<root>/cost/<target>/<run>/             shs_cost.json
<root>/magic/<config>/<run>/            magic.json
```

`kofta-stats` reads that tree and prints LaTeX rows that drop into the paper.
Cells with no run data stay as the `[\;]` placeholder, so a partial campaign
yields an honest, compilable, incomplete table — **numbers are never invented.**

```shell
$ ./kofta-stats <campaign_root>                 # all tables (metric=edges)
$ ./kofta-stats <campaign_root> --table cov     # one table
$ ./kofta-stats <campaign_root> --targets smoke # score a custom/pilot target
```

## Docker, smoke test & pilot

[`docker/`](docker/) contains a reproducible Ubuntu 20.04 environment and two
self-checking harnesses, both runnable in CI:

| Script | What it proves |
|---|---|
| `docker/run-smoke.sh` | KOFTA builds, instruments [`docker/smoke.c`](docker/smoke.c), and the SHS seam fires (`afl-fuzz` queries `kofta-shs` and gets candidates back, against `--mock`). |
| `docker/run-pilot.sh` | the **whole** offline pipeline — `kofta-campaign` → post-process → `kofta-stats` — produces real (non-placeholder) table rows from real artifacts on the toy `smoke` target. |

```shell
$ docker build -t kofta-smoke docker/
$ docker run --rm kofta-smoke                 # runs run-smoke.sh
```

Both harnesses also run on GitHub Actions (`.github/workflows/`) in a real
`ubuntu:20.04` container, gating changes to the instrumentation, the SHS seam,
and the evaluation scripts.

## Repository layout

```
afl-fuzz.c, afl-*.c        AFL core + KOFTA multi-argument forkserver / taint inference
llvm_mode/                 afl-clang-fast instrumentation (option list + branch srcmap)
kofta-shs                  SHS query endpoint (LLM <-> afl-fuzz seam)
kofta-campaign             evaluation matrix runner
kofta-stats                LaTeX table / facts emitter
kofta-opts, kofta-plot     option tabulation + coverage plots
shs/                       SHS Python package: service, campaign, loaders, tables, tests
docker/                    Ubuntu 20.04 build + smoke/pilot harnesses
docs/                      upstream AFL documentation
```

## Built on AFL

KOFTA is a fork of **american fuzzy lop** (AFL 2.57b), originally by Michal
Zalewski `<lcamtuf@google.com>`, and inherits its instrumentation, genetic
queue, and tooling. The upstream AFL documentation is preserved under
[`docs/`](docs/) — see `docs/QuickStartGuide.txt`, `docs/technical_details.txt`,
`docs/env_variables.txt`, and `llvm_mode/README.llvm`. KOFTA is distributed
under the same **Apache License 2.0** (see [`docs/COPYING`](docs/COPYING)).
