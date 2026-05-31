#!/usr/bin/env bash
# Runs inside the container (see docker/Dockerfile CMD). Builds KOFTA from the
# read-only /repo bind mount, compiles the tiny instrumented target, runs a
# short kofta-fuzz campaign with the SHS C seam launching one long-lived
# `kofta-shs serve` co-process (NDJSON over a pipe) against the offline --mock
# client, and asserts that the seam actually fired.
#
# PASS criterion: the KOFTA_DEBUG log contains at least one "shs_cand,..." line,
# proving afl-fuzz.c queried kofta-shs and got candidates back. Finding the
# planted crash is a bonus (printed but not required, since fork-timing varies).
set -euo pipefail

REPO=/repo
BUILD=/build
WORK=/work

echo "==> copying repo (read-only mount) into writable $BUILD"
rm -rf "$BUILD"
cp -r "$REPO" "$BUILD"
cd "$BUILD"

echo "==> building afl-fuzz (KOFTA_DEBUG=1)"
# AFL_NO_X86 skips the legacy GCC-mode x86 assembly self-test; the container is
# arm64 and we only use the arch-independent llvm_mode (afl-clang-fast) path.
make clean >/dev/null
AFL_NO_X86=1 make CC=clang-12 KOFTA_DEBUG=1

echo "==> building llvm_mode instrumentation"
make -C llvm_mode LLVM_CONFIG=llvm-config-12 CC=clang-12 CXX=clang++-12

echo "==> compiling instrumented smoke target"
mkdir -p "$WORK/in"
rm -f "$WORK/opts.txt" "$WORK/srcmap.txt"
KOFTA_OPTSAVE="$WORK/opts.txt" \
KOFTA_SRCMAP="$WORK/srcmap.txt" \
AFL_PATH="$BUILD" AFL_CC=clang-12 \
  "$BUILD/afl-clang-fast" -g "$BUILD/docker/smoke.c" -o "$WORK/smoke"

echo "==> srcmap written by the LLVM pass:"
sed -n '1,40p' "$WORK/srcmap.txt" || true
echo "==> options discovered by the pass:"
cat "$WORK/opts.txt" || true

printf 'hello\n' > "$WORK/in/seed"

echo "==> running kofta-fuzz (60s, SHS via --mock, no API)"
cd "$WORK"
rm -f "$WORK/KOFTA_DEBUG"
set +e
AFL_SKIP_CPUFREQ=1 \
AFL_I_DONT_CARE_ABOUT_MISSING_CRASHES=1 \
PYTHONPATH="$BUILD" \
KOFTA_SRCMAP="$WORK/srcmap.txt" \
KOFTA_SHS=1 \
KOFTA_SHS_BIN="$BUILD/kofta-shs" \
KOFTA_SHS_CACHE="$WORK/cache.json" \
KOFTA_SHS_COST="$WORK/shs_cost.json" \
  timeout 180 "$BUILD/kofta-fuzz" -i "$WORK/in" -o "$WORK/out" \
    -m none -t 5000 \
    -k "$WORK/opts.txt" -- "$WORK/smoke" >"$WORK/fuzz.log" 2>&1
rc=$?
set -e

echo "==> kofta-fuzz exit code: $rc (124 = timed out = ran full duration)"
echo "----- kofta-fuzz output (full) ------------------------------------"
cat "$WORK/fuzz.log" 2>/dev/null || echo "  (fuzz.log missing)"
echo "----- /work/out contents ------------------------------------------"
ls -la "$WORK/out" 2>/dev/null || echo "  (no out dir)"

echo "==> SHS-related KOFTA_DEBUG lines (shs_init / shs_call / shs_cand):"
grep -E "shs_init|shs_call|shs_cand|shs_noslice" "$WORK/KOFTA_DEBUG" 2>/dev/null || \
  echo "  (none -- the STR-hint block was never reached)"

echo "==> SHS cost record (proves the serve co-process flushed --cost-out):"
cat "$WORK/shs_cost.json" 2>/dev/null || echo "  (no shs_cost.json -- serve never shut down cleanly)"

if grep -q "shs_cand" "$WORK/KOFTA_DEBUG" 2>/dev/null; then
  echo "==> PASS: SHS C seam fired (kofta-shs queried, candidates returned)"
else
  echo "==> FAIL: no shs_cand lines -- the SHS C seam never fired" >&2
  echo "----- diagnostics -------------------------------------------------" >&2
  echo "[fuzzer_stats]" >&2
  cat "$WORK/out/fuzzer_stats" 2>/dev/null | grep -E "execs_done|execs_per_sec|cycles_done|paths_total" >&2 || true
  echo "[KOFTA_DEBUG tail]" >&2
  tail -n 20 "$WORK/KOFTA_DEBUG" 2>/dev/null >&2 || echo "  (no KOFTA_DEBUG file)" >&2
  echo "[fuzz.log tail]" >&2
  tail -n 25 "$WORK/fuzz.log" 2>/dev/null >&2 || true
  exit 1
fi

if ls "$WORK/out/crashes/"id* >/dev/null 2>&1; then
  echo "==> BONUS: planted crash reproduced"
else
  echo "==> (no crash this run -- timing-dependent, not a failure)"
fi
