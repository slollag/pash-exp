#!/usr/bin/env python3
"""Power-experiment harness: PaSh vs Bash energy/time measurement.

Usage:
    sudo /home/aaronzp/dev/pash-exp/.venv/bin/python3 \
        experiments/power/harness.py [options]

The harness runs every (benchmark, runtime, size) condition once per
epoch in randomized order. For each timed run it captures:
  - wall-clock time (monotonic)
  - integrated RAPL energy (with overflow handling)
  - 10 Hz energy samples for offline analysis
  - resource usage from /usr/bin/time -v

Outputs are written to results/{runs.csv,samples.csv,compile_runs.csv,
metadata.json,logs/}. Files are flushed after every row so partial
runs are usable if the experiment is interrupted.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config as cfg  # noqa: E402
import rapl  # noqa: E402

REPO_ROOT = cfg.REPO_ROOT
RESULTS_DEFAULT = HERE / "results"
ARTIFACTS_SUBDIR = "precompiled"
LOGS_SUBDIR = "logs"
HARNESS_VERSION = "1.0.0"


# ----- Run records ---------------------------------------------------------

RUNS_FIELDS = [
    "run_id", "epoch", "trial_index_in_epoch", "timestamp_iso",
    "benchmark", "runtime", "input_size", "input_path",
    "wall_clock_s", "exit_code",
    "energy_pkg_uj_total", "energy_core_uj_total",
    "avg_power_pkg_w", "avg_power_core_w",
    "user_cpu_s", "sys_cpu_s", "max_rss_kb",
    "num_rapl_samples",
    "governor", "cpu_freq_pre_mhz", "cpu_freq_post_mhz",
    "cpu_temp_pre_c", "cpu_temp_post_c",
    "notes",
]

SAMPLES_FIELDS = [
    "run_id", "sample_index", "monotonic_s",
    "energy_pkg_uj", "delta_pkg_uj", "power_pkg_w_inst",
    "energy_core_uj", "delta_core_uj", "power_core_w_inst",
]

COMPILE_FIELDS = [
    "run_id", "epoch", "benchmark", "input_size",
    "wall_clock_s", "energy_pkg_uj_total", "exit_code", "timestamp_iso",
]


# ----- System inspection ---------------------------------------------------

def read_first_line(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip().splitlines()[0]
    except Exception:
        return None


def cpu_freq_mhz() -> Optional[float]:
    val = read_first_line(Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq"))
    return float(val) / 1000 if val else None


def cpu_governor() -> Optional[str]:
    return read_first_line(Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"))


def cpu_temp_c() -> Optional[float]:
    """Best-effort CPU temp from thermal_zone with type ~ 'cpu' or 'k10temp'."""
    for tz in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        ttype = read_first_line(tz / "type") or ""
        if any(k in ttype.lower() for k in ("cpu", "x86_pkg_temp", "k10temp", "tctl", "tdie")):
            raw = read_first_line(tz / "temp")
            if raw:
                return float(raw) / 1000.0
    return None


def discover_pash_top() -> Optional[str]:
    """Return PASH_TOP path (the pash python package dir) or None."""
    try:
        out = subprocess.check_output(
            [sys.executable, "-c", "import pash, os; print(os.path.dirname(pash.__file__))"],
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


# ----- Env construction ----------------------------------------------------

def base_env(input_path: Path) -> dict:
    """Construct a minimal env for benchmark subprocesses.

    sudo wipes most env vars, so we rebuild what we need here. We
    pull PATH from the venv first so that 'pash' resolves correctly.
    """
    venv_bin = REPO_ROOT / ".venv" / "bin"
    sys_path = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    env = {
        "PATH": f"{venv_bin}:{sys_path}",
        "IN": str(input_path),
        "HOME": os.environ.get("SUDO_HOME") or os.path.expanduser("~"),
        # dict.txt ships sorted under en_US.UTF-8 collation, so spell.sh's
        # final `comm -23` step requires this locale to agree. Using C or
        # C.UTF-8 makes comm reject the dictionary as out of order.
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": "/tmp",
    }
    pash_top = discover_pash_top()
    if pash_top:
        env["PASH_TOP"] = pash_top
    return env


# ----- Subprocess timing ---------------------------------------------------

@dataclass
class TimeResult:
    wall_clock_s: float
    exit_code: int
    user_cpu_s: float
    sys_cpu_s: float
    max_rss_kb: int
    samples: list[rapl.Sample]
    integrated: dict[str, dict]


GNU_TIME = "/usr/bin/time"
HAVE_GNU_TIME = Path(GNU_TIME).exists()


def parse_gnu_time(stderr_path: Path) -> tuple[float, float, int]:
    """Pull user, sys, and max RSS from a GNU time -v stderr file."""
    user = sys_t = 0.0
    rss = 0
    text = stderr_path.read_text(errors="replace")
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"User time \(seconds\): (.+)", line)
        if m:
            user = float(m.group(1))
        m = re.match(r"System time \(seconds\): (.+)", line)
        if m:
            sys_t = float(m.group(1))
        m = re.match(r"Maximum resident set size \(kbytes\): (\d+)", line)
        if m:
            rss = int(m.group(1))
    return user, sys_t, rss


def rusage_children_delta(before, after) -> tuple[float, float, int]:
    """Compute child user/sys CPU + max RSS delta from two getrusage(RUSAGE_CHILDREN) snapshots."""
    user = (after.ru_utime - before.ru_utime)
    sys_t = (after.ru_stime - before.ru_stime)
    # ru_maxrss is the high-water mark for ALL children since process start
    # on Linux (in kB). Taking the post-run value is a reasonable proxy when
    # no other children ran concurrently.
    rss = max(0, after.ru_maxrss)
    return user, sys_t, rss


def run_command_timed(
    cmd: list[str],
    env: dict,
    domains: list[rapl.RaplDomain],
    stdout_path: Path,
    stderr_path: Path,
    sample_hz: float,
    cwd: Optional[Path] = None,
) -> TimeResult:
    """Run cmd to completion, sampling RAPL throughout; return measurements."""
    import resource

    sampler = rapl.SamplerThread(domains, hz=sample_hz)
    sampler.start()
    t0 = time.monotonic()

    if HAVE_GNU_TIME:
        full_cmd = [GNU_TIME, "-v", "--", *cmd]
    else:
        full_cmd = list(cmd)

    ru_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    with stdout_path.open("wb") as out_f, stderr_path.open("wb") as err_f:
        proc = subprocess.Popen(full_cmd, env=env, stdout=out_f, stderr=err_f, cwd=cwd)
        rc = proc.wait()
    ru_after = resource.getrusage(resource.RUSAGE_CHILDREN)

    wall = time.monotonic() - t0
    sampler.stop()
    sampler.join(timeout=2.0)

    integrated = rapl.integrate(sampler.samples, domains)
    if HAVE_GNU_TIME:
        user_t, sys_t, rss = parse_gnu_time(stderr_path)
    else:
        user_t, sys_t, rss = rusage_children_delta(ru_before, ru_after)
    return TimeResult(
        wall_clock_s=wall,
        exit_code=rc,
        user_cpu_s=user_t,
        sys_cpu_s=sys_t,
        max_rss_kb=rss,
        samples=sampler.samples,
        integrated=integrated,
    )


# ----- PaSh pre-compilation ------------------------------------------------

def precompile_pash(benchmark: str, size: str, artifacts_dir: Path,
                    domains: list[rapl.RaplDomain], logs_dir: Path,
                    epoch: int, compile_writer) -> Optional[Path]:
    """Run pash --dry_run_compiler and stash /tmp/optimized_script.sh.

    The compile cost (time + energy) is logged to compile_runs.csv.
    Returns the path to the stashed artifact, or None on failure.
    """
    script = cfg.ONELINERS / f"{benchmark}.sh"
    in_path = cfg.INPUTS / cfg.SIZES[size]
    env = base_env(in_path)

    run_id = f"compile-{benchmark}-{size}-{epoch}-{uuid.uuid4().hex[:6]}"
    stdout_p = logs_dir / f"{run_id}.stdout"
    stderr_p = logs_dir / f"{run_id}.stderr"

    # Remove any stale optimized script so we can detect success.
    cfg.PASH_OPTIMIZED_OUT.unlink(missing_ok=True)

    cmd = ["pash", "--dry_run_compiler", str(script)]
    res = run_command_timed(cmd, env, domains, stdout_p, stderr_p, cfg.SAMPLE_HZ)

    pkg_total = res.integrated.get("package-0", {}).get("total_uj", 0)
    compile_writer.writerow({
        "run_id": run_id,
        "epoch": epoch,
        "benchmark": benchmark,
        "input_size": size,
        "wall_clock_s": f"{res.wall_clock_s:.6f}",
        "energy_pkg_uj_total": pkg_total,
        "exit_code": res.exit_code,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })

    if res.exit_code != 0 or not cfg.PASH_OPTIMIZED_OUT.exists():
        print(f"  ! precompile failed for {benchmark}/{size} (rc={res.exit_code})", flush=True)
        return None

    artifacts_dir.mkdir(parents=True, exist_ok=True)
    dest = artifacts_dir / f"{benchmark}_{size}.sh"
    shutil.copy2(cfg.PASH_OPTIMIZED_OUT, dest)
    dest.chmod(0o755)
    return dest


# ----- Trial dispatch ------------------------------------------------------

def build_command(trial: cfg.Trial, warm_artifact: Optional[Path]) -> list[str]:
    if trial.runtime == "bash":
        return ["bash", str(trial.script_path())]
    if trial.runtime == "pash_cold":
        return ["pash", str(trial.script_path())]
    if trial.runtime == "pash_warm":
        if warm_artifact is None:
            raise RuntimeError(f"warm artifact missing for {trial.benchmark}/{trial.size}")
        return ["bash", str(warm_artifact)]
    raise ValueError(f"unknown runtime {trial.runtime!r}")


def run_trial(
    trial: cfg.Trial,
    warm_artifact: Optional[Path],
    domains: list[rapl.RaplDomain],
    logs_dir: Path,
    epoch: int,
    trial_idx: int,
    runs_writer,
    samples_writer,
) -> None:
    run_id = f"e{epoch:02d}-t{trial_idx:03d}-{trial.benchmark}-{trial.runtime}-{trial.size}-{uuid.uuid4().hex[:6]}"
    in_path = trial.input_path()
    env = base_env(in_path)
    cmd = build_command(trial, warm_artifact)

    stdout_p = logs_dir / f"{run_id}.stdout"
    stderr_p = logs_dir / f"{run_id}.stderr"

    gov = cpu_governor()
    f_pre = cpu_freq_mhz()
    t_pre = cpu_temp_c()

    print(f"  [{trial.runtime:9s}] {trial.benchmark}/{trial.size} ...", end="", flush=True)
    res = run_command_timed(cmd, env, domains, stdout_p, stderr_p, cfg.SAMPLE_HZ)
    print(f" {res.wall_clock_s:7.2f}s rc={res.exit_code}", flush=True)

    f_post = cpu_freq_mhz()
    t_post = cpu_temp_c()

    pkg = res.integrated.get("package-0", {})
    core = res.integrated.get("core", {})

    runs_writer.writerow({
        "run_id": run_id,
        "epoch": epoch,
        "trial_index_in_epoch": trial_idx,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark": trial.benchmark,
        "runtime": trial.runtime,
        "input_size": trial.size,
        "input_path": str(in_path),
        "wall_clock_s": f"{res.wall_clock_s:.6f}",
        "exit_code": res.exit_code,
        "energy_pkg_uj_total": pkg.get("total_uj", ""),
        "energy_core_uj_total": core.get("total_uj", ""),
        "avg_power_pkg_w": f"{pkg.get('avg_power_w', 0):.4f}" if pkg else "",
        "avg_power_core_w": f"{core.get('avg_power_w', 0):.4f}" if core else "",
        "user_cpu_s": res.user_cpu_s,
        "sys_cpu_s": res.sys_cpu_s,
        "max_rss_kb": res.max_rss_kb,
        "num_rapl_samples": len(res.samples),
        "governor": gov or "",
        "cpu_freq_pre_mhz": f"{f_pre:.1f}" if f_pre else "",
        "cpu_freq_post_mhz": f"{f_post:.1f}" if f_post else "",
        "cpu_temp_pre_c": f"{t_pre:.2f}" if t_pre is not None else "",
        "cpu_temp_post_c": f"{t_post:.2f}" if t_post is not None else "",
        "notes": "",
    })

    write_samples(samples_writer, run_id, res, domains)


def write_samples(writer, run_id: str, res: TimeResult, domains: list[rapl.RaplDomain]) -> None:
    pkg_deltas = res.integrated.get("package-0", {}).get("deltas_uj", [])
    core_deltas = res.integrated.get("core", {}).get("deltas_uj", [])
    for i, s in enumerate(res.samples):
        pkg_uj = s.readings_uj.get("package-0", "")
        core_uj = s.readings_uj.get("core", "")
        d_pkg = pkg_deltas[i] if i < len(pkg_deltas) else 0
        d_core = core_deltas[i] if i < len(core_deltas) else 0
        # Instantaneous power: delta over interval. The first sample has
        # no prior, so power is left blank.
        if i == 0:
            p_pkg = p_core = ""
        else:
            dt = s.monotonic_s - res.samples[i - 1].monotonic_s
            p_pkg = f"{(d_pkg / 1_000_000) / dt:.4f}" if dt > 0 else ""
            p_core = f"{(d_core / 1_000_000) / dt:.4f}" if dt > 0 else ""
        writer.writerow({
            "run_id": run_id,
            "sample_index": i,
            "monotonic_s": f"{s.monotonic_s:.4f}",
            "energy_pkg_uj": pkg_uj,
            "delta_pkg_uj": d_pkg,
            "power_pkg_w_inst": p_pkg,
            "energy_core_uj": core_uj,
            "delta_core_uj": d_core,
            "power_core_w_inst": p_core,
        })


# ----- Idle baselines ------------------------------------------------------

def idle_baseline(label: str, duration_s: int, domains: list[rapl.RaplDomain],
                  runs_writer, samples_writer, logs_dir: Path,
                  epoch: int = 0, trial_idx: int = -1) -> None:
    run_id = f"idle-{label}-{epoch:02d}-{trial_idx:03d}-{uuid.uuid4().hex[:6]}"
    print(f"  [idle     ] {label} ({duration_s}s)...", end="", flush=True)

    sampler = rapl.SamplerThread(domains, hz=cfg.SAMPLE_HZ)
    sampler.start()
    t0 = time.monotonic()
    time.sleep(duration_s)
    wall = time.monotonic() - t0
    sampler.stop()
    sampler.join(timeout=2.0)

    integrated = rapl.integrate(sampler.samples, domains)
    pkg = integrated.get("package-0", {})
    core = integrated.get("core", {})
    print(f" pkg={pkg.get('avg_power_w', 0):.2f}W core={core.get('avg_power_w', 0):.2f}W", flush=True)

    runs_writer.writerow({
        "run_id": run_id,
        "epoch": epoch,
        "trial_index_in_epoch": trial_idx,
        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "benchmark": f"idle_{label}",
        "runtime": "idle",
        "input_size": "",
        "input_path": "",
        "wall_clock_s": f"{wall:.6f}",
        "exit_code": 0,
        "energy_pkg_uj_total": pkg.get("total_uj", ""),
        "energy_core_uj_total": core.get("total_uj", ""),
        "avg_power_pkg_w": f"{pkg.get('avg_power_w', 0):.4f}" if pkg else "",
        "avg_power_core_w": f"{core.get('avg_power_w', 0):.4f}" if core else "",
        "user_cpu_s": 0,
        "sys_cpu_s": 0,
        "max_rss_kb": 0,
        "num_rapl_samples": len(sampler.samples),
        "governor": cpu_governor() or "",
        "cpu_freq_pre_mhz": "",
        "cpu_freq_post_mhz": "",
        "cpu_temp_pre_c": "",
        "cpu_temp_post_c": "",
        "notes": "idle baseline",
    })

    fake_res = TimeResult(
        wall_clock_s=wall, exit_code=0,
        user_cpu_s=0, sys_cpu_s=0, max_rss_kb=0,
        samples=sampler.samples, integrated=integrated,
    )
    write_samples(samples_writer, run_id, fake_res, domains)


# ----- Environment checks --------------------------------------------------

def ensure_inputs() -> None:
    have = all((cfg.INPUTS / fname).exists() for fname in cfg.SIZES.values())
    if have:
        return
    print("Generating large input files (10M, 100M, 1G)...", flush=True)
    subprocess.check_call(["bash", str(cfg.INPUTS / "multiply.sh")])


def check_environment() -> tuple[list[rapl.RaplDomain], dict]:
    if os.geteuid() != 0:
        print("ERROR: must run as root (RAPL energy_uj is mode 0400). Use sudo.", file=sys.stderr)
        sys.exit(1)

    domains = rapl.discover_domains()
    if not domains:
        print("ERROR: no readable RAPL domains found.", file=sys.stderr)
        sys.exit(1)

    if not HAVE_GNU_TIME:
        print("NOTE: /usr/bin/time not installed; falling back to getrusage(RUSAGE_CHILDREN).",
              file=sys.stderr)
        print("      For richer per-run metrics, install with: sudo apt install time",
              file=sys.stderr)

    if shutil.which("pash", path=str(REPO_ROOT / ".venv" / "bin") + ":" + os.environ.get("PATH", "")) is None:
        print("ERROR: 'pash' not found in venv.", file=sys.stderr)
        sys.exit(1)

    pash_top = discover_pash_top()
    info = {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "cpu_model": read_first_line(Path("/proc/cpuinfo")),  # rough
        "governor": cpu_governor(),
        "cpu_freq_mhz_at_start": cpu_freq_mhz(),
        "rapl_domains": [
            {"name": d.name, "path": str(d.path), "max_energy_uj": d.max_energy_uj}
            for d in domains
        ],
        "pash_top": pash_top,
        "have_gnu_time": HAVE_GNU_TIME,
        "harness_version": HARNESS_VERSION,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    return domains, info


# ----- Main ----------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="PaSh power-experiment harness")
    p.add_argument("--epochs", type=int, default=cfg.DEFAULT_EPOCHS)
    p.add_argument("--results-dir", type=Path, default=RESULTS_DEFAULT)
    p.add_argument("--benchmarks", nargs="+", default=cfg.BENCHMARKS,
                   choices=cfg.BENCHMARKS)
    p.add_argument("--runtimes", nargs="+", default=cfg.RUNTIMES,
                   choices=cfg.ALL_RUNTIMES)
    p.add_argument("--sizes", nargs="+", default=list(cfg.SIZES.keys()),
                   choices=list(cfg.SIZES.keys()))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--skip-baselines", action="store_true",
                   help="Skip pre/post idle baselines (still does inter-trial idle)")
    p.add_argument("--dry-run", action="store_true",
                   help="Print schedule and exit; no measurements")
    return p.parse_args()


def open_writer(path: Path, fields: list[str]):
    f = path.open("a", newline="")
    writer = csv.DictWriter(f, fieldnames=fields)
    if path.stat().st_size == 0:
        writer.writeheader()
    return f, writer


def main() -> int:
    args = parse_args()

    args.results_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = args.results_dir / LOGS_SUBDIR
    artifacts_dir = args.results_dir / ARTIFACTS_SUBDIR
    logs_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    trials = [
        cfg.Trial(b, r, s)
        for b in args.benchmarks
        for r in args.runtimes
        for s in args.sizes
    ]
    if not trials:
        print("No trials selected.", file=sys.stderr)
        return 1

    print(f"Schedule: {args.epochs} epochs x {len(trials)} trials = {args.epochs * len(trials)} runs")
    print(f"  benchmarks: {args.benchmarks}")
    print(f"  runtimes:   {args.runtimes}")
    print(f"  sizes:      {args.sizes}")
    print(f"  results:    {args.results_dir}")

    if args.dry_run:
        rng = random.Random(args.seed)
        for ep in range(1, args.epochs + 1):
            order = trials[:]
            rng.shuffle(order)
            print(f"\n-- epoch {ep} --")
            for i, t in enumerate(order):
                print(f"  {i:3d}. {t.runtime:9s} {t.benchmark}/{t.size}")
        return 0

    domains, meta = check_environment()
    ensure_inputs()

    (args.results_dir / "metadata.json").write_text(json.dumps(meta, indent=2))

    runs_path = args.results_dir / "runs.csv"
    samples_path = args.results_dir / "samples.csv"
    compile_path = args.results_dir / "compile_runs.csv"
    runs_f, runs_w = open_writer(runs_path, RUNS_FIELDS)
    samples_f, samples_w = open_writer(samples_path, SAMPLES_FIELDS)
    compile_f, compile_w = open_writer(compile_path, COMPILE_FIELDS)

    def flush_all():
        runs_f.flush(); samples_f.flush(); compile_f.flush()

    rng = random.Random(args.seed)

    try:
        if not args.skip_baselines:
            print("\n== pre-experiment idle baseline ==")
            idle_baseline("pre", cfg.IDLE_PRE_S, domains, runs_w, samples_w, logs_dir)
            flush_all()

        # Cache of warm artifacts: (benchmark, size) -> path
        warm_cache: dict[tuple[str, str], Path] = {}
        need_warm = "pash_warm" in args.runtimes

        for ep in range(1, args.epochs + 1):
            print(f"\n== epoch {ep}/{args.epochs} ==")

            if need_warm:
                # Pre-compile artifacts for this epoch (cost logged separately).
                for b in args.benchmarks:
                    for s in args.sizes:
                        if (b, s) in warm_cache and warm_cache[(b, s)].exists():
                            continue
                        print(f"  compile pash {b}/{s} ...", flush=True)
                        path = precompile_pash(b, s, artifacts_dir, domains, logs_dir, ep, compile_w)
                        if path is not None:
                            warm_cache[(b, s)] = path
                        flush_all()

            order = trials[:]
            rng.shuffle(order)

            for i, t in enumerate(order):
                # Inter-trial idle (also acts as cooldown).
                idle_baseline("intertrial", cfg.IDLE_BETWEEN_S, domains, runs_w, samples_w,
                              logs_dir, epoch=ep, trial_idx=i)
                warm_artifact = warm_cache.get((t.benchmark, t.size)) if t.runtime == "pash_warm" else None
                if t.runtime == "pash_warm" and warm_artifact is None:
                    print(f"  [pash_warm] SKIP {t.benchmark}/{t.size} (no compiled artifact)", flush=True)
                    continue
                try:
                    run_trial(t, warm_artifact, domains, logs_dir, ep, i, runs_w, samples_w)
                except Exception as e:
                    print(f"  ! trial failed: {e}", flush=True)
                flush_all()

        if not args.skip_baselines:
            print("\n== post-experiment idle baseline ==")
            idle_baseline("post", cfg.IDLE_POST_S, domains, runs_w, samples_w, logs_dir)
            flush_all()
    finally:
        runs_f.close(); samples_f.close(); compile_f.close()

    print("\nDone. Results in", args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
