#!/bin/bash
# Find all 2-grams in a piece of text
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs/1G.txt}}"

. "$SUITE_DIR/bi-gram.aux.sh"

cat "$IN" |
  tr -c 'A-Za-z' '[\n*]' |
  grep -v "^\s*$" |
  tr A-Z a-z |
  bigrams_aux |
  sort |
  uniq
