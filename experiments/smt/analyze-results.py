import argparse
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt


MODE_BASELINE = "pash_1core_1thread"
MODE_SMT = "pash_1core_2smt_threads"
MODE_TWO_CORE = "pash_2cores_1thread_each"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="Path to smt_results.csv")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    out_dir = csv_path.parent / "graphs"
    out_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(csv_path)


    # Median runtime
    med = (
        df.groupby(["benchmark", "input", "mode"], as_index=False)
        ["elapsed_sec"].median()
    )

    # Put modes into columns
    pivot = med.pivot_table(
        index=["benchmark", "input"],
        columns="mode",
        values="elapsed_sec",
        aggfunc="first",
    ).reset_index()

    # Speedups relative to PaSh width 1
    pivot["smt_speedup"] = pivot[MODE_BASELINE] / pivot[MODE_SMT]
    pivot["two_core_speedup"] = pivot[MODE_BASELINE] / pivot[MODE_TWO_CORE]

    # 1.0 means SMT got the same speedup as two real cores.
    # Lower means SMT fell behind two real cores.
    pivot["close_to_two_core_ratio"] = (
        pivot["smt_speedup"] / pivot["two_core_speedup"]
    )

    # Average over input sizes for each benchmark.
    table = (
        pivot.groupby("benchmark", as_index=False)
        .agg(close_to_two_core_ratio=("close_to_two_core_ratio", "median"))
        .sort_values("close_to_two_core_ratio")
    )

    # Save table.
    table_path = out_dir / "close_to_two_core_ratio_by_benchmark.csv"
    table.to_csv(table_path, index=False)

    # Plot graph.
    plt.figure(figsize=(10, max(4, 0.35 * len(table))))
    plt.barh(table["benchmark"], table["close_to_two_core_ratio"])

    plt.xlabel("SMT speedup / two-core speedup")
    plt.ylabel("Benchmark")
    plt.title("Close-to-two-core ratio by benchmark")
    plt.legend()
    plt.grid(axis="x", alpha=0.3)
    plt.tight_layout()

    graph_path = out_dir / "close_to_two_core_ratio_by_benchmark.png"
    plt.savefig(graph_path, dpi=200)
    plt.close()

    print(f"Wrote table: {table_path}")
    print(f"Wrote graph: {graph_path}")


if __name__ == "__main__":
    main()