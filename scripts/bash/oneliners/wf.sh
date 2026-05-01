#!/bin/bash
# Calculate the frequency of each word in the document, and sort by frequency
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs/1M.txt}}"

cat "$IN" | tr -c 'A-Za-z' '[\n*]' | grep -v "^\s*$" | tr A-Z a-z | sort | uniq -c | sort -rn
