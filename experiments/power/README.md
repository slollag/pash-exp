# Power Experiment Harness

Measures wall-clock time, RAPL package + core energy, and resource usage
for `wf`, `spell`, `nfa-regex`, and `sort-sort` under `bash` and `pash`
(JIT each run) at input sizes 10M / 100M / 1G.

> **PaSh JIT note**: PaSh 0.16.6 uses a daemon-driven JIT model — there's
> no single artifact to "compile once and re-run". The `pash_warm` runtime
> (pre-compiled artifact) was planned but isn't viable on this version. To
> isolate JIT compile cost, you'd need to instrument PaSh internally.

## One-time setup

```bash
# Install GNU time if missing
sudo apt install time

# (Optional) install stress-ng for the AMD RAPL sanity step
sudo apt install stress-ng

# Generate the large input files (~1.1 GB)
bash scripts/inputs/multiply.sh
```

## Run

The harness must run as root because `/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj` is mode `0400`.

```bash
# Schedule preview (no measurements):
.venv/bin/python3 experiments/power/harness.py --dry-run

# Smoke test: 1 epoch, one benchmark, one size — ~5 minutes:
sudo .venv/bin/python3 experiments/power/harness.py \
    --epochs 1 --benchmarks wf --sizes 10M --skip-baselines

# Full experiment: 5 epochs x 24 trials = 120 timed runs, ~3–5 hours:
sudo .venv/bin/python3 experiments/power/harness.py
```

`Ctrl-C` is safe — files are flushed after every row, so partial data is usable.

## CLI flags

| Flag | Default | Notes |
|---|---|---|
| `--epochs N` | 5 | Each epoch runs every condition once in random order. |
| `--benchmarks B...` | all four | Subset for testing. |
| `--runtimes R...` | bash, pash_cold | `pash_warm` accepted but skipped (see note above). |
| `--sizes S...` | 10M, 100M, 1G | |
| `--seed N` | 42 | Controls trial-order randomization. |
| `--skip-baselines` | off | Skip the 120 s pre/post idle reads. |
| `--dry-run` | off | Print schedule and exit. |
| `--results-dir PATH` | `./results` | Where to write outputs. |

## Outputs

```
results/
├── runs.csv           # one row per timed run + idle baselines
├── samples.csv        # 10 Hz RAPL samples for every run
├── compile_runs.csv   # PaSh JIT compile cost (one row per precompile)
├── metadata.json      # host, kernel, governor, RAPL domains, PaSh version
├── precompiled/       # cached optimized scripts for pash_warm
└── logs/              # per-run stdout / stderr (incl. /usr/bin/time -v)
```

The harness does **no aggregation** — every measurement lands in CSV as-is.

### Suggested post-hoc analyses

- Median + IQR + coefficient of variation per (benchmark, runtime, size). CV > ~10% means more trials are warranted.
- **Energy hypothesis test**: per (benchmark, size), compare median `energy_pkg_uj_total` between `bash` and `pash_cold`. PaSh hypothesis predicts pash_cold > bash despite shorter wall_clock.
- **Work-attributable energy**: subtract `idle_pre.avg_power_pkg_w * wall_clock_s` from each run's total to estimate energy attributable to the workload itself, controlling for the host's idle floor (~production Docker activity).
- **Energy-delay product**: `wall_clock_s * energy_pkg_uj_total / 1e6` (J·s) — single metric balancing speed vs energy.
- **Time-series sanity**: plot `samples.csv` `power_pkg_w_inst` vs `monotonic_s` for one trial per condition. Look for truncation, anomalies, or unexpected idle periods.

## Quirks

- **AMD RAPL caveat**: this host is a Ryzen 7 2700 (Zen+). The kernel exposes `intel-rapl:0` (package-0) and `intel-rapl:0:0` (core), but no DRAM domain. Energy values come from AMD's RAPL implementation, which may differ slightly from Intel's measurement methodology — treat absolute values as approximate; comparisons within the same host are still valid.
- **Production Docker host**: avoid scheduling other heavy workloads while runs are in flight. The pre/post idle baselines will catch drift, but won't recover lost signal.
- **PaSh artifact paths**: pre-compiled scripts in `results/precompiled/` may reference `PASH_TOP`-relative helpers from the venv. They're tied to this machine and venv — do not relocate.
- **RAPL counter overflow**: `max_energy_range_uj` ≈ 65.5 GJ, wraps every ~55–60 s at typical socket power. The sampler runs at 10 Hz and computes deltas with wraparound handling, so runs of any duration are fine.
