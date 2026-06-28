#!/usr/bin/env sh
set -eu

if [ -n "${JAI_COMPILER_PATH:-}" ]; then
    JAI_COMPILER="$JAI_COMPILER_PATH"
elif [ -x /root/programming/jai/bin/jai-linux ]; then
    JAI_COMPILER=/root/programming/jai/bin/jai-linux
else
    JAI_COMPILER=jai
fi

cd "$(dirname "$0")"
"$JAI_COMPILER" build.jai
./tests/test_ledger_core

