#!/bin/bash
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs/1M.txt}}"

cat "$IN" | sort
