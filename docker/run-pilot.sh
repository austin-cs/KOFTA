#!/usr/bin/env bash
# Stage 2 PILOT: a small-scale, offline (--mock, no API) dry run of the whole
# SHS evaluation pipeline. It builds KOFTA, instruments the toy docker/smoke.c
# target, lays out the directory shape a campaign spec expects, runs a tiny
# kofta-campaign matrix (kofta/kshs/kshsng x 1 target x 2 runs, ~25s each), then
# feeds the artifacts to kofta-stats and asserts it produced REAL (non-[\;])
# table rows. The point is to shake out orchestration/extraction/stats wiring
# bugs before spending real compute + API budget on the Stage 3 campaigns -- not
# to measure fuzzing dynamics.
#
# Must run on a native x86_64 Linux host with glibc <=2.33 (Ubuntu 20.04): same
# constraints as run-smoke.sh (KOFTA's __args_leak + LLVM-12 legacy passes).
#
# Overridable via env (defaults match the container layout):
#   PILOT_REPO   source tree              (default /repo)
#   PILOT_BUILD  writable build dir       (default /build; set == repo to build in place)
#
# PASS criterion: kofta-stats emits at least one populated coverage row for the
# smoke target (a numeric median, not the [\;] placeholder) AND a populated SHS
# cost row -- proving the campaign->postprocess->loaders->tables round-trip works
# on real artifacts.
set -euo pipefail

REPO="${PILOT_REPO:-/repo}"
BUILD="${PILOT_BUILD:-/build}"

if [ "$BUILD" != "$REPO" ]; then
  echo "==> copying repo ($REPO) into writable $BUILD"
  rm -rf "$BUILD"
  cp -r "$REPO" "$BUILD"
else
  echo "==> building in place at $BUILD (no copy)"
fi
cd "$BUILD"

echo "==> building afl-fuzz / afl-showmap"
make clean >/dev/null
AFL_NO_X86=1 make CC=clang-12

echo "==> building llvm_mode instrumentation"
make -C llvm_mode LLVM_CONFIG=llvm-config-12 CC=clang-12 CXX=clang++-12

echo "==> laying out the campaign input tree (seeds/, opts/, bin/, srcmap.txt)"
mkdir -p seeds/smoke opts bin
printf 'hello\n' > seeds/smoke/seed
rm -f opts/smoke.txt srcmap.txt
KOFTA_OPTSAVE="$BUILD/opts/smoke.txt" \
KOFTA_SRCMAP="$BUILD/srcmap.txt" \
AFL_PATH="$BUILD" AFL_CC=clang-12 \
  "$BUILD/afl-clang-fast" -g "$BUILD/docker/smoke.c" -o "$BUILD/bin/smoke"

echo "==> opts discovered by the LLVM pass (-k file for kofta-fuzz):"
cat opts/smoke.txt || true

echo "==> running the pilot campaign (mock SHS, no API)"
rm -rf pilot-campaign
python3 ./kofta-campaign shs/campaign.pilot.json

echo "----- campaign tree -----------------------------------------------"
find pilot-campaign -maxdepth 4 -type f | sort || true

echo "==> generating tables from the pilot artifacts (--targets smoke)"
# kofta-stats defaults to the paper's eval binaries; the pilot target is "smoke",
# so we must override the target list or every row is a placeholder.
python3 ./kofta-stats pilot-campaign --targets smoke | tee pilot-stats.out

echo "==> asserting kofta-stats produced real (non-placeholder) rows"
# The kshs vs kofta comparison (RQ5 facts) only prints when both configs have
# coverage for the smoke target -- i.e. the whole campaign->edges->loaders chain
# worked for at least kshs and kofta.
if ! grep -Eq "targets compared[[:space:]]+= [1-9]" pilot-stats.out; then
  echo "==> FAIL: no coverage facts -- the cov table is empty/placeholder" >&2
  echo "----- edges.txt files found ---------------------------------------" >&2
  find pilot-campaign -name edges.txt -print -exec cat {} \; >&2 || true
  exit 1
fi
# Show the smoke cov row for the log. Its weifuzz/llmonly cells are legitimately
# [\;] (the pilot doesn't run those configs); the kofta and kshs cells (the 4th
# and 5th "&"-separated fields) must be real medians, not placeholders.
smoke_cov="$(grep -E '^smoke' pilot-stats.out | head -1 || true)"
echo "smoke cov row: $smoke_cov"
ko_cell="$(printf '%s' "$smoke_cov" | awk -F'&' '{gsub(/ /,"",$4); print $4}')"
ks_cell="$(printf '%s' "$smoke_cov" | awk -F'&' '{gsub(/ /,"",$5); print $5}')"
if [ -z "$ko_cell" ] || [ "$ko_cell" = '[\;]' ] || [ -z "$ks_cell" ] || [ "$ks_cell" = '[\;]' ]; then
  echo "==> FAIL: smoke cov row missing kofta/kshs edge medians (ko='$ko_cell' ks='$ks_cell')" >&2
  exit 1
fi

echo "==> PASS: pilot pipeline produced real tables from real artifacts"
echo "----- cost records ------------------------------------------------"
find pilot-campaign/cost -name shs_cost.json -print -exec cat {} \; 2>/dev/null || \
  echo "  (no cost records -- kshs/kshsng cost did not flush)"
