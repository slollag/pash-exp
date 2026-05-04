"""Experiment configuration: benchmarks, runtimes, sizes, trial counts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ONELINERS = REPO_ROOT / "scripts" / "bash" / "oneliners"
INPUTS = REPO_ROOT / "scripts" / "inputs"

BENCHMARKS = ["wf", "spell", "nfa-regex", "sort-sort"]

# PaSh 0.16.6 uses a JIT daemon and doesn't emit a single reusable
# /tmp/optimized_script.sh, so the "warm" variant isn't viable on this
# version. Kept as an opt-in for future PaSh versions that support
# ahead-of-time compilation: pass --runtimes bash pash_cold pash_warm.
RUNTIMES = ["bash", "pash_cold"]
ALL_RUNTIMES = ["bash", "pash_cold", "pash_warm"]

# Maps a logical size label to the input filename in scripts/inputs/.
SIZES = {
    "1M": "1M.txt",
    "10M": "10M.txt",
    "100M": "100M.txt",
}

DEFAULT_EPOCHS = 5

# Sampling cadence for the RAPL thread.
SAMPLE_HZ = 10.0

# Idle baseline durations (seconds).
IDLE_PRE_S = 120
IDLE_POST_S = 120
IDLE_BETWEEN_S = 5

# Path PaSh writes its compiled artifact to (per CLAUDE.md).
PASH_OPTIMIZED_OUT = Path("/tmp/optimized_script.sh")


@dataclass(frozen=True)
class Trial:
    benchmark: str
    runtime: str
    size: str

    def script_path(self) -> Path:
        return ONELINERS / f"{self.benchmark}.sh"

    def input_path(self) -> Path:
        return INPUTS / SIZES[self.size]


def all_trials() -> list[Trial]:
    return [
        Trial(b, r, s)
        for b in BENCHMARKS
        for r in RUNTIMES
        for s in SIZES
    ]
