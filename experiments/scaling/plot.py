import sys
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit


def amdahl(n: float, p: float) -> float:
    return 1.0 / ((1 - p) + p / n)


def main(csv_path: Path, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.read_csv(csv_path)

    agg = df.groupby(['benchmark', 'shell', 'width'])['wall_real_s'].agg(['mean', 'std', 'count']).reset_index()
    agg.to_csv(out_dir / 'aggregated.csv', index=False)
    print(agg.to_string())

    bash_times = agg[agg['shell'] == 'bash'].set_index('benchmark')['mean']
    pash = agg[agg['shell'] == 'pash'].copy()
    pash['speedup'] = pash.apply(lambda r: bash_times[r['benchmark']] / r['mean'], axis=1)

    fig, ax = plt.subplots(figsize=(8, 6))
    benchmarks = sorted(pash['benchmark'].unique())
    fits = {}

    for bench in benchmarks:
        sub = pash[pash['benchmark'] == bench].sort_values('width')
        widths = sub['width'].values
        speedups = sub['speedup'].values
        line, = ax.plot(widths, speedups, 'o-', label=bench, markersize=8)

        try:
            popt, _ = curve_fit(amdahl, widths, speedups, bounds=(0, 1))
            p_fit = popt[0]
            fits[bench] = p_fit
            xs = np.linspace(1, widths.max(), 100)
            ax.plot(xs, amdahl(xs, p_fit), '--', color=line.get_color(), alpha=0.4,
                    label=f'  fit p={p_fit:.2f}')
        except Exception as e:
            print(f"bad amdahl on {bench} {e}")

    ax.axhline(1, color='gray', linestyle=':', label='bash baseline')
    ax.set_xlabel('cores')
    ax.set_ylabel('speedup vs bash')
    ax.set_title('bash vs PaSh across cores')
    ax.set_xscale('log', base=2)
    ax.set_xticks([1, 2, 4, 8, 16])
    ax.set_xticklabels([1, 2, 4, 8, 16])
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / 'speedup_vs_cores.png', dpi=150)

    fig, ax = plt.subplots(figsize=(8, 6))
    for bench in benchmarks:
        sub_pash = pash[pash['benchmark'] == bench].sort_values('width')
        ax.plot(sub_pash['width'], sub_pash['mean'], 'o-', label=f'{bench} (pash)')
        ax.axhline(bash_times[bench], linestyle='--', alpha=0.5,
                   label=f'{bench} (bash)')
    ax.set_xlabel('cores')
    ax.set_ylabel('wall clock (s)')
    ax.set_title('bash vs PaSh wall clock time')
    ax.set_xscale('log', base=2)
    ax.set_yscale('log')
    ax.set_xticks([1, 2, 4, 8, 16])
    ax.set_xticklabels([1, 2, 4, 8, 16])
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_dir / 'wallclock_vs_cores.png', dpi=150)

    for bench, p in fits.items():
        max_speedup = 1 / (1 - p) if p < 1 else float('inf')
        print(f"  {bench}: p={p:.3f}  (max possible speedup = {max_speedup:.2f}x)")


if __name__ == '__main__':
    csv = Path(sys.argv[1] if len(sys.argv) > 1 else 'scaling_results.csv')
    out = Path(sys.argv[2] if len(sys.argv) > 2 else 'plots')
    main(csv, out)
