#!/bin/sh
# Builds bin/byse_pow_armv7: a static ARMv7 hard-float binary (Vu+ DUO4K and
# other cortex-a15/a9 boxes). Needs zig: pip install ziglang
set -e
cd "$(dirname "$0")/.."
python3 -m ziglang cc -target arm-linux-musleabihf -mcpu=cortex_a15 -O2 -static -s \
    -o bin/byse_pow_armv7 tools/byse_pow.c
python3 -m ziglang cc -target aarch64-linux-musl -O2 -static -s \
    -o bin/byse_pow_aarch64 tools/byse_pow.c
ls -l bin/byse_pow_*
