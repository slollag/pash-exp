"""RAPL energy sampler.

Reads Intel-RAPL-style energy_uj counters from /sys/class/powercap/.
On AMD Zen+/Zen2/Zen3 the kernel exposes the same interface via the
generic powercap driver, so this works on the Ryzen 7 2700 host too,
modulo the AMD-specific accuracy caveats noted in the experiment plan.

Two responsibilities:
  1. SamplerThread: runs in the background and pushes 10 Hz samples
     into a queue while a benchmark subprocess executes.
  2. integrate_samples(): converts raw uj readings into per-sample
     deltas with overflow handling, plus a total energy figure.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path

RAPL_ROOT = Path("/sys/class/powercap/intel-rapl")


@dataclass(frozen=True)
class RaplDomain:
    name: str
    path: Path
    max_energy_uj: int

    def read_uj(self) -> int:
        return int((self.path / "energy_uj").read_text().strip())


def discover_domains() -> list[RaplDomain]:
    """Return all readable RAPL domains under intel-rapl:0.

    Includes the package and any subdomains (core, uncore, dram) that
    the kernel exposes. Skips domains whose energy_uj is unreadable.
    """
    domains: list[RaplDomain] = []
    pkg_dir = RAPL_ROOT / "intel-rapl:0"
    if not pkg_dir.exists():
        raise RuntimeError(f"RAPL not available at {pkg_dir}")

    candidates = [pkg_dir] + sorted(pkg_dir.glob("intel-rapl:0:*"))
    for d in candidates:
        try:
            (d / "energy_uj").read_text()
        except (PermissionError, FileNotFoundError):
            continue
        name = (d / "name").read_text().strip()
        max_uj = int((d / "max_energy_range_uj").read_text().strip())
        domains.append(RaplDomain(name=name, path=d, max_energy_uj=max_uj))
    return domains


@dataclass
class Sample:
    monotonic_s: float
    readings_uj: dict[str, int]


class SamplerThread(threading.Thread):
    """Background thread that polls all RAPL domains at a fixed rate."""

    def __init__(self, domains: list[RaplDomain], hz: float = 10.0):
        super().__init__(daemon=True)
        self.domains = domains
        self.interval = 1.0 / hz
        self.samples: list[Sample] = []
        self._stop_evt = threading.Event()
        self._t0: float | None = None

    def run(self) -> None:
        self._t0 = time.monotonic()
        next_t = self._t0
        while not self._stop_evt.is_set():
            now = time.monotonic()
            readings = {d.name: d.read_uj() for d in self.domains}
            self.samples.append(Sample(monotonic_s=now - self._t0, readings_uj=readings))
            next_t += self.interval
            sleep = next_t - time.monotonic()
            if sleep > 0:
                self._stop_evt.wait(sleep)
            else:
                # We fell behind; resync.
                next_t = time.monotonic()

    def stop(self) -> None:
        self._stop_evt.set()


def integrate(samples: list[Sample], domains: list[RaplDomain]) -> dict[str, dict]:
    """Return per-domain integrated energy with overflow handling.

    For each domain, walks the sample list in order. Whenever the
    counter decreases we treat it as a wraparound and add max_energy_uj
    to the delta. Returns:
        { domain_name: {
            "total_uj": int,
            "duration_s": float,
            "avg_power_w": float,
            "deltas_uj": list[int],   # one per sample (first is 0)
        } }
    """
    if not samples:
        return {d.name: {"total_uj": 0, "duration_s": 0.0, "avg_power_w": 0.0, "deltas_uj": []} for d in domains}

    duration = samples[-1].monotonic_s - samples[0].monotonic_s
    out: dict[str, dict] = {}
    for d in domains:
        deltas = [0]
        total = 0
        prev = samples[0].readings_uj[d.name]
        for s in samples[1:]:
            cur = s.readings_uj[d.name]
            if cur >= prev:
                delta = cur - prev
            else:
                delta = (cur + d.max_energy_uj) - prev
            deltas.append(delta)
            total += delta
            prev = cur
        avg_power = (total / 1_000_000) / duration if duration > 0 else 0.0
        out[d.name] = {
            "total_uj": total,
            "duration_s": duration,
            "avg_power_w": avg_power,
            "deltas_uj": deltas,
        }
    return out
