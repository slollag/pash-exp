#!/bin/bash
# Match complex regular-expression over input
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs/1G.txt}}"

cat "$IN" | tr A-Z a-z | grep '\(.\).*\1\(.\).*\2\(.\).*\3\(.\).*\4'
