# Local runtime verification image for the SHS C path.
#
# clang/llvm 12 is mandatory: KOFTA's LLVM passes register via the legacy pass
# manager (RegisterStandardPasses) and use the single-arg CreateLoad overload.
# clang 13+ defaults to the new PM and silently skips our instrumentation;
# LLVM 14 dropped the single-arg CreateLoad. Ubuntu 22.04 ships clang-12.
# KOFTA's runtime (kofta-llvm-rt.o.c) leaks argv/argc via hardcoded x86_64
# inline asm ("lea 0x50(%rsp)"), so this image must run on an x86_64 Docker
# host. On Apple Silicon, start Colima as an x86_64 VM (see docker/smoke.sh):
#     colima start --arch x86_64
# We deliberately do NOT use `FROM --platform=...` -- that needs BuildKit/buildx
# for cross-builds, which the brew `docker` CLI doesn't ship. A native x86_64 VM
# builds this natively with the classic builder instead.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang-12 llvm-12-dev make libc6-dev python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

CMD ["bash", "/repo/docker/run-smoke.sh"]
