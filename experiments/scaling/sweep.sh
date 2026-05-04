#!/bin/bash
#SBATCH --job-name=pash-scaling
#SBATCH --partition=compute
#SBATCH --nodelist=echidna
#SBATCH --exclusive
#SBATCH --time=06:00:00
#SBATCH --output=results/scaling/sweep-%j.out
#SBATCH --error=results/scaling/sweep-%j.err

declare -A INPUT_FOR
INPUT_FOR[wf]=~/pash-exp/scripts/inputs/1G.txt
INPUT_FOR[spell]=~/pash-exp/scripts/inputs/1G.txt
INPUT_FOR[nfa-regex]=~/pash-exp/scripts/inputs/10M.txt
INPUT_FOR[sort-sort]=~/pash-exp/scripts/inputs/1G.txt

BENCHMARKS=("wf" "spell" "nfa-regex" "sort-sort")
WIDTHS=(1 2 4 8 16)
TRIALS=3

CSV=results/scaling/scaling_results.csv
echo "benchmark,shell,width,trial,wall_real_s,user_s,sys_s" > $CSV

extract_time() {
  # time output from min+sec to sec
  local f=$1
  local field=$2
  grep "^${field}" "$f" | awk '{print $2}' | \
    awk -F'm' '{ if (NF==2) print $1*60 + substr($2,1,length($2)-1); else print substr($1,1,length($1)-1) }'
}

for trial in $(seq 1 $TRIALS); do
  for bench in "${BENCHMARKS[@]}"; do
    INPUT=${INPUT_FOR[$bench]}
    SCRIPT=scripts/bash/oneliners/${bench}.sh

    # bash
    LOG=results/scaling/${bench}_bash_w1_t${trial}.time
    { time IN=$INPUT taskset -c 0 bash $SCRIPT > /dev/null ; } 2> $LOG || true
    real=$(extract_time $LOG real)
    user=$(extract_time $LOG user)
    sys=$(extract_time $LOG sys)
    echo "${bench},bash,1,${trial},${real},${user},${sys}" >> $CSV
    echo "[$(date +%T)] $bench bash trial=$trial: ${real}s"

    # pash
    for w in "${WIDTHS[@]}"; do
      CORES=$(seq -s, 0 $((w-1)))
      LOG=results/scaling/${bench}_pash_w${w}_t${trial}.time
      { time IN=$INPUT taskset -c $CORES pash --width $w $SCRIPT > /dev/null ; } 2> $LOG || true
      real=$(extract_time $LOG real)
      user=$(extract_time $LOG user)
      sys=$(extract_time $LOG sys)
      echo "${bench},pash,${w},${trial},${real},${user},${sys}" >> $CSV
      echo "[$(date +%T)] $bench pash w=$w trial=$trial: ${real}s"
    done
  done
done

echo "done"
