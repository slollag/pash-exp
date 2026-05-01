#!/bin/bash
# Calculate sort twice
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs/1G.txt}}"

cat "$IN" | tr A-Z a-z | sort | sort -r
