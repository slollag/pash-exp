#!/bin/bash
SUITE_DIR="$(realpath "$(dirname "$0")")"
BASE="$SUITE_DIR/1M.txt"

if [[ ! -f "$BASE" ]]; then
  echo "Missing base file: $BASE" >&2
  exit 1
fi

make_repeated_file() {
  local copies="$1"
  local out="$2"

  if [[ -f "$out" ]]; then
    echo "Already exists: $out"
    return
  fi

  echo "Creating $out from $copies copies of 1M.txt..."
  : > "$out"

  for _ in $(seq 1 "$copies"); do
    cat "$BASE" >> "$out"
  done
}

make_repeated_file 10   "$SUITE_DIR/10M.txt"
make_repeated_file 100  "$SUITE_DIR/100M.txt"
make_repeated_file 1024 "$SUITE_DIR/1G.txt"

echo "Done."
ls -lh "$SUITE_DIR"/{1M,10M,100M,1G}.txt