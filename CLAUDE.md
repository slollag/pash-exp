# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CS1952Y course project evaluating [PaSh](https://github.com/binpash/pash), a shell script parallelization JIT compiler, on different hardware configurations. PaSh transforms POSIX shell scripts to execute in a data-parallel fashion, scheduling parallelizable tasks over multiple threads. The repo contains the benchmark scripts (classic Unix oneliners) and tooling to run them under both PaSh and vanilla Bash.

### Research Questions & Hypotheses

1. **Core scaling**: Computation time should decrease as core count increases under PaSh (but not vanilla Bash), with diminishing returns per Amdahl's law. The saturation point estimates the parallelizable fraction of each workload.
2. **SMT vs. multi-core**: On a single-core SMT processor with 2 threads, performance gains should approach a true 2-core processor only for non-IO-bound scripts. IO-bound workloads should see smaller gains because a 2-core processor has more IO capacity.
3. **Energy efficiency**: PaSh completes faster but may consume more total energy due to JIT overhead. The question is whether PaSh is meaningfully less energy-efficient.
4. **Cache sensitivity**: Benchmarks with large per-process state (e.g., spell check with a dictionary) should degrade under PaSh when cache is constrained so only one copy fits, since parallel threads compete for cache. Serial Bash should not show this effect.

### Experiment Division

- **Aaron**: Power/energy experiment using RAPL profiling on Linux.
- **Sebastian**: SMT experiment — comparing co-located SMT threads vs. separate cores on Hydra.
- **Dru**: Core-scaling baseline experiment (non-SMT).

### Infrastructure

- **Hydra** (Brown CS cluster): 24-core Intel Xeon Gold 5220S nodes with SMT (48 logical cores), 256 GB RAM. Slurm for job scheduling. Connect via `ssh.cs.brown.edu` — Slurm commands run directly from there, no separate login node. Used by Sebastian and Dru for SMT and core-scaling experiments.
- **gem5**: Originally considered, abandoned — 20-minute boot times, container/loop-device limitations, and dependency issues on dept. machines.
- **Aaron's power experiment**: Running on a personal Arch Linux machine (SSH remote). No cluster needed since the experiment is purely sequential Bash vs. PaSh — no hardware simulation required. RAPL (Running Average Power Limit) reads energy from `/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj`. May need `sudo chmod -R o+r /sys/class/powercap/intel-rapl/` or run as root to access it.

### Power Experiment Plan (Aaron)

**Hypothesis**: PaSh finishes faster but consumes more total energy (Joules) than sequential Bash due to JIT overhead and multi-core spin-up. Energy = power × time, so even with lower wall-clock time, total Joules may be higher.

**Measurement approach**:
- Read RAPL energy counter before and after each benchmark run
- Record: wall-clock time, total energy (Joules), derived average power (W)
- Run each benchmark 3–5 times for variance; use large inputs (1G.txt) for signal
- Minimize background load on the machine during runs (close other apps)
- RAPL measures the whole CPU socket — ensure no other heavy processes are running

**Key subtlety**: RAPL counter overflows ~every 60 seconds. For benchmarks running longer than that, use a background sampling loop with overflow handling rather than a simple before/after read.

**Still to confirm**: Verify PaSh is installable on Arch (the pip packages in `requirements.txt` should cover it, but the `pash` CLI runtime binary also needs to be present).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key dependency: `pash` (the shell script parallelizer) and its ecosystem (`libdash`, `libbash`, `shasta`, `sh-expand`, `pash-annotations`).

## Running Scripts

Each oneliner in `scripts/bash/oneliners/` accepts input via the `IN` environment variable or as the first argument, defaulting to a file in `scripts/inputs/`:

```bash
# Run with default input
bash scripts/bash/oneliners/wf.sh

# Run with custom input
IN=/path/to/file bash scripts/bash/oneliners/spell.sh
```

To generate larger input files (10M, 100M, 1G) from the base 1M.txt:

```bash
bash scripts/inputs/multiply.sh
```

## Post-Compilation Tool

`tools/make-compiled-script-runnable.py` patches PaSh-compiled scripts so they can run standalone (e.g., under gem5). It adds `mkdir -p` commands for FIFO directories and rewrites runtime paths:

```bash
python3 tools/make-compiled-script-runnable.py <compiled_script> <output_script> <old_runtime_path> <new_runtime_path>
```

PaSh saves compiled output to `/tmp/optimized_script.sh` by default.

## Architecture

- `scripts/bash/oneliners/` — Shell pipelines that serve as PaSh benchmarks. Each is self-contained except `bi-grams.sh` which sources `bi-gram.aux.sh` for helper functions.
- `scripts/inputs/` — Input data files. `1M.txt` is the base; `dict.txt` is the spell-check dictionary. `multiply.sh` generates larger inputs.
- `tools/` — Post-processing utilities for PaSh-compiled output.
