#!/bin/bash
SUITE_DIR="$(realpath "$(dirname "$0")")"
IN="${IN:-${1:-$SUITE_DIR/../../inputs}}"
comm "$IN/file1" "$IN/file2"
