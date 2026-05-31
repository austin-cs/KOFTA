# Local runtime verification image for the SHS C path.
#
# clang/llvm 12 is mandatory: KOFTA's LLVM passes register via the legacy pass
# manager (RegisterStandardPasses) and use the single-arg CreateLoad overload.
# clang 13+ defaults to the new PM and silently skips our instrumentation;
# LLVM 14 dropped the single-arg CreateLoad. Ubuntu 22.04 ships clang-12.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        clang-12 llvm-12-dev make libc6-dev python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

CMD ["bash", "/repo/docker/run-smoke.sh"]
