#!/bin/bash
#SBATCH --job-name=smt-pash
#SBATCH --nodelist=smblade16a1
#SBATCH --cpus-per-task=48
#SBATCH --time=08:00:00
#SBATCH --exclusive

set -euo pipefail

# Submit script with sbatch if not already on a machine
if [[ -z "${SLURM_JOB_ID:-}" ]]; then
    echo "Submitting with sbatch:"
    exec sbatch "$0" "$@"
fi

HERE="/home/slgallo/comp-arch/pash-exp/experiments/smt"
ROOT="$(cd "$HERE/../.." && pwd)"

BENCH_DIR="$ROOT/scripts/bash/oneliners"
INPUT_DIR="$ROOT/scripts/inputs"
RESULTS_DIR="$HERE/results"

mkdir -p "$RESULTS_DIR"

OUT_CSV="$RESULTS_DIR/smt_results.csv"

# Output all runs to same ltmp file
TMP_OUT="${TMP_OUT:-/ltmp/${USER:-slgallo}/smt_pash_tmp.out}"
mkdir -p "$(dirname "$TMP_OUT")"
: > "$TMP_OUT"

INPUTS=(
    "$INPUT_DIR/1M.txt"
    "$INPUT_DIR/10M.txt"
    "$INPUT_DIR/100M.txt"
    "$INPUT_DIR/1G.txt"
)

TRIALS="${TRIALS:-5}"

PASH="${PASH:-pash}"

# Pinning based on the lscpu output:
# CPU 0 and CPU 24 are SMT siblings on core 0.
# CPU 0 and CPU 1 are separate physical cores.
ONE_THREAD_CPUS="0"
SMT_CPUS="0,24"
TWO_CORE_CPUS="0,1"

BLACKLIST="${BLACKLIST:-}"
mapfile -t BENCHMARKS < <(
    find "$BENCH_DIR" -maxdepth 1 -type f -name "*.sh" | sort | while read -r script; do
        name="$(basename "$script")"

        skip=false
        for bad in $BLACKLIST; do
            if [[ "$name" == "$bad" ]]; then
                skip=true
                break
            fi
        done

        if [[ "$skip" == false ]]; then
            echo "$script"
        fi
    done
)

if (( ${#BENCHMARKS[@]} == 0 )); then
    echo "ERROR: no benchmarks found"
    exit 1
fi

echo "Found ${#BENCHMARKS[@]} benchmark scripts."
printf '  %s\n' "${BENCHMARKS[@]}"
echo

echo "benchmark,input,input_size_bytes,mode,engine,width,cpus,trial,elapsed_sec,exit_status" > "$OUT_CSV"

run_one() {
    local bench="$1"
    local input="$2"
    local mode="$3"
    local engine="$4"
    local width="$5"
    local cpus="$6"
    local trial="$7"

    local bench_name
    bench_name="$(basename "$bench" .sh)"

    local input_name
    input_name="$(basename "$input")"

    local input_size
    input_size="$(stat -c '%s' "$input")"

    local start_ns
    local end_ns
    local elapsed
    local status

    echo "[$(date '+%F %T')] benchmark=$bench_name input=$input_name mode=$mode engine=$engine width=$width cpus=$cpus trial=$trial"

    start_ns="$(date +%s%N)"

    set +e
    if [[ "$engine" == "bash" ]]; then
        taskset -c "$cpus" \
            env IN="$input" OUT="$TMP_OUT" \
            bash "$bench" "$input" \
            >"$TMP_OUT" 2>&1
        status=$?
    else # pash
        taskset -c "$cpus" \
            env IN="$input" OUT="$TMP_OUT" \
            "$PASH" -w "$width" "$bench" "$input" \
            >"$TMP_OUT" 2>&1
        status=$?
    fi
    set -e

    end_ns="$(date +%s%N)"
    elapsed="$(awk -v s="$start_ns" -v e="$end_ns" 'BEGIN { printf "%.6f", (e - s) / 1000000000 }')"

    echo "$bench_name,$input_name,$input_size,$mode,$engine,$width,\"$cpus\",$trial,$elapsed,$status" >> "$OUT_CSV"

    if [[ "$status" -ne 0 ]]; then
        echo "  WARNING: command failed with status $status"
        echo "  Last overwritten output is in: $TMP_OUT"
    fi
}

for trial in $(seq 1 "$TRIALS"); do
    for bench in "${BENCHMARKS[@]}"; do
        for input in "${INPUTS[@]}"; do

            run_one "$bench" "$input" \
                "bash_1core_1thread" \
                "bash" \
                "1" \
                "$ONE_THREAD_CPUS" \
                "$trial"

            # PaSh width 1 baseline
            run_one "$bench" "$input" \
                "pash_1core_1thread" \
                "pash" \
                "1" \
                "$ONE_THREAD_CPUS" \
                "$trial"

            # Main SMT case:
            # width 2, but both hardware threads are on the same physical core.
            run_one "$bench" "$input" \
                "pash_1core_2smt_threads" \
                "pash" \
                "2" \
                "$SMT_CPUS" \
                "$trial"

            # Main two-core comparison:
            # width 2, with one hardware thread on each of two physical cores.
            run_one "$bench" "$input" \
                "pash_2cores_1thread_each" \
                "pash" \
                "2" \
                "$TWO_CORE_CPUS" \
                "$trial"

        done
    done
done

echo
echo "Done."
echo "Results written to: $OUT_CSV"
echo "Temporary output reused at: $TMP_OUT"