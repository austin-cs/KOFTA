# Local runtime verification image for the SHS C path.
#
# Ubuntu 20.04 is load-bearing (not just for clang-12). KOFTA's runtime
# (kofta-llvm-rt.o.c) leaks argv/argc via a hardcoded stack offset
# ("lea 0x50(%rsp)", +0xc) tuned for glibc <=2.33's __libc_csu_init startup.
# glibc 2.34 (Ubuntu 22.04) refactored startup, so on 22.04 the offset points
# at garbage and the forkserver dies on the dry run before any fuzzing happens.
# 20.04 ships glibc 2.31 (offset valid) AND clang-12, which is also mandatory:
# KOFTA's LLVM passes register via the legacy pass manager (RegisterStandard-
# Passes) and use the single-arg CreateLoad overload; clang 13+ defaults to the
# new PM and silently skips our instrumentation, and LLVM 14 dropped single-arg
# CreateLoad.
# The argv-leak asm is x86_64-only, so this image must run on an x86_64 Docker
# host. On Apple Silicon, start Colima as an x86_64 VM (see docker/smoke.sh):
#     colima start --arch x86_64
# We deliberately do NOT use `FROM --platform=...` -- that needs BuildKit/buildx
# for cross-builds, which the brew `docker` CLI doesn't ship. A native x86_64 VM
# builds this natively with the classic builder instead.
FROM ubuntu:20.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang-12 llvm-12-dev make libc6-dev python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

CMD ["bash", "/repo/docker/run-smoke.sh"]
