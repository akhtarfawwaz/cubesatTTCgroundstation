#!/usr/bin/env python3
"""
Analysis of the repeated 100-packet range-test runs.

Reads range_test_logs/summary_100pkt.csv. If that file is missing it is rebuilt
from the raw run_{d}m_r{n}_{stamp}.csv files, replayed through the same Run100
counting rule the live logger uses -- so expected is always 100, CRC-FAIL
frames are never counted as received, and p_id values outside the run window
are ignored.

Produces:
  stdout                                  one Markdown table per distance,
                                          each run plus a pooled row
  range_test_logs/analysis/pdr_100pkt.png PDR vs distance, 95 % Wilson CI bars
  range_test_logs/analysis/pdr_100pkt.csv the pooled table, machine-readable

Pooling note: the pooled PDR is total received / total expected across runs,
not the mean of the per-run percentages. Those differ whenever a run is cut
short, and the ratio-of-totals is the right estimator for a binomial rate. The
mean and SD of the per-run PDRs are reported alongside it, because the spread
between repeats at one distance is what shows whether the link is stable or
marginal.

Run:  backend/venv/Scripts/python.exe Receiver/tools/analyze_100pkt.py
"""

import csv
import re
import statistics as st
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")           # no display on this box; write files only
import matplotlib.pyplot as plt

from range_logger import (CSV_FIELDS, LEGACY_FIELD_COUNT, LOG_DIR,
                          RUN_PACKETS, Run100, wilson_ci)

ANALYSIS_DIR = LOG_DIR / "analysis"
SUMMARY_100 = LOG_DIR / "summary_100pkt.csv"
RUN_RE = re.compile(r"^run_(?P<dist>[0-9.]+)m_r(?P<run>[^_]+)_(?P<stamp>\d{8}_\d{6})\.csv$")


def _f(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def from_summary(path):
    """Rows straight out of summary_100pkt.csv."""
    out = []
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            dist = _f(r.get("distance_m"))
            if dist is None:
                continue
            received = int(_f(r.get("received"), 0))
            expected = int(_f(r.get("expected"), RUN_PACKETS) or RUN_PACKETS)
            out.append({
                "distance": dist,
                "run": (r.get("run") or "").strip() or "?",
                "received": received,
                "expected": expected,
                "crc_fail": int(_f(r.get("crc_fail"), 0)),
                "pdr": _f(r.get("pdr_pct"), 100.0 * received / expected),
                "rssi": _f(r.get("rssi_mean")),
                "snr": _f(r.get("snr_mean")),
                "cadence": _f(r.get("cadence_s")),
                "notes": (r.get("notes") or "").strip(),
                "source": "summary",
            })
    return out


def from_run_files(log_dir):
    """Rebuild the per-run figures by replaying the raw CSVs."""
    out = []
    for path in sorted(log_dir.glob("run_*m_r*_*.csv")):
        m = RUN_RE.match(path.name)
        if not m:
            continue
        run = Run100()
        with open(path, newline="", encoding="utf-8") as fh:
            rd = csv.reader(fh)
            for parts in rd:
                if not parts or parts[0] == "rx_ms":
                    continue
                # Raw rows may carry the trailing pc_time column or not.
                if len(parts) not in (len(CSV_FIELDS), LEGACY_FIELD_COUNT,
                                      len(CSV_FIELDS) + 1):
                    continue
                try:
                    rx_ms = int(parts[0])
                    pid = int(parts[1])
                    rssi = int(parts[2])
                    snr = float(parts[3])
                    crc_ok = parts[4] == "OK"
                except ValueError:
                    continue
                run.add(pid, rssi, snr, crc_ok, rx_ms)
        out.append({
            "distance": float(m.group("dist")),
            "run": m.group("run"),
            "received": run.received,
            "expected": run.expected,
            "crc_fail": run.crc_fail,
            "pdr": run.pdr,
            "rssi": (sum(run.rssi) / len(run.rssi)) if run.rssi else None,
            "snr": (sum(run.snr) / len(run.snr)) if run.snr else None,
            "cadence": run.cadence or None,
            "notes": "" if run.fresh_boot else "not_fresh_boot",
            "source": path.name,
        })
    return out


def pooled(rows):
    tot_rx = sum(r["received"] for r in rows)
    tot_exp = sum(r["expected"] for r in rows)
    pdr = 100.0 * tot_rx / tot_exp if tot_exp else 0.0
    lo, hi = wilson_ci(tot_rx, tot_exp)
    per_run = [r["pdr"] for r in rows]
    rssis = [r["rssi"] for r in rows if r["rssi"] is not None]
    snrs = [r["snr"] for r in rows if r["snr"] is not None]
    return {
        "runs": len(rows),
        "received": tot_rx,
        "expected": tot_exp,
        "pdr": pdr,
        "ci_low": lo,
        "ci_high": hi,
        "pdr_mean": st.mean(per_run) if per_run else 0.0,
        "pdr_sd": st.stdev(per_run) if len(per_run) > 1 else 0.0,
        "rssi_mean": st.mean(rssis) if rssis else None,
        "snr_mean": st.mean(snrs) if snrs else None,
        "crc_fail": sum(r["crc_fail"] for r in rows),
    }


def fmt(value, spec, dash="—"):
    return dash if value is None else format(value, spec)


def main():
    if SUMMARY_100.exists():
        rows = from_summary(SUMMARY_100)
        origin = SUMMARY_100.name
        if not rows:
            rows, origin = from_run_files(LOG_DIR), "run_*.csv (summary empty)"
    else:
        rows, origin = from_run_files(LOG_DIR), "run_*.csv (no summary yet)"

    if not rows:
        print("No 100-packet runs found.")
        print(f"Looked for {SUMMARY_100} and {LOG_DIR}/run_*m_r*_*.csv")
        return 1

    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    by_dist = {}
    for r in rows:
        by_dist.setdefault(r["distance"], []).append(r)

    print(f"# Repeated 100-packet range tests\n")
    print(f"Source: `{origin}`  ·  {len(rows)} runs  ·  "
          f"{len(by_dist)} distance(s)  ·  {RUN_PACKETS} packets per run\n")

    pooled_rows = []
    for dist in sorted(by_dist):
        runs = sorted(by_dist[dist], key=lambda r: str(r["run"]))
        p = pooled(runs)
        pooled_rows.append((dist, p))

        print(f"## {dist:g} m\n")
        print("| Run | Received / 100 | PDR % | CRC fail | RSSI dBm | SNR dB | "
              "Cadence s | Notes |")
        print("|---|---|---|---|---|---|---|---|")
        for r in runs:
            print(f"| {r['run']} | {r['received']} / {r['expected']} | "
                  f"{r['pdr']:.2f} | {r['crc_fail']} | "
                  f"{fmt(r['rssi'], '.1f')} | {fmt(r['snr'], '.2f')} | "
                  f"{fmt(r['cadence'], '.3f')} | {r['notes'] or ''} |")
        print(f"| **pooled ({p['runs']} runs)** | **{p['received']} / "
              f"{p['expected']}** | **{p['pdr']:.2f}** "
              f"(95 % CI {p['ci_low']:.2f}–{p['ci_high']:.2f}) | "
              f"{p['crc_fail']} | {fmt(p['rssi_mean'], '.1f')} | "
              f"{fmt(p['snr_mean'], '.2f')} | — | "
              f"per-run mean {p['pdr_mean']:.2f} %, SD {p['pdr_sd']:.2f} |")
        print()

    # ---- machine-readable pooled table ----------------------------------
    out_csv = ANALYSIS_DIR / "pdr_100pkt.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["distance_m", "runs", "received", "expected", "pdr_pct",
                    "ci_low_pct", "ci_high_pct", "pdr_mean_pct", "pdr_sd_pct",
                    "rssi_mean_dbm", "snr_mean_db", "crc_fail"])
        for dist, p in pooled_rows:
            w.writerow([dist, p["runs"], p["received"], p["expected"],
                        round(p["pdr"], 2), round(p["ci_low"], 2),
                        round(p["ci_high"], 2), round(p["pdr_mean"], 2),
                        round(p["pdr_sd"], 2),
                        "" if p["rssi_mean"] is None else round(p["rssi_mean"], 1),
                        "" if p["snr_mean"] is None else round(p["snr_mean"], 2),
                        p["crc_fail"]])

    # ---- plot ------------------------------------------------------------
    xs = [d for d, _ in pooled_rows]
    ys = [p["pdr"] for _, p in pooled_rows]
    lo = [max(0.0, p["pdr"] - p["ci_low"]) for _, p in pooled_rows]
    hi = [max(0.0, p["ci_high"] - p["pdr"]) for _, p in pooled_rows]

    fig, ax = plt.subplots(figsize=(6.4, 4.0), dpi=160)
    ax.errorbar(xs, ys, yerr=[lo, hi], fmt="o-", capsize=5, linewidth=1.4,
                markersize=6, color="#1f4e79", ecolor="#1f4e79",
                label="pooled PDR, 95 % Wilson CI")
    # Individual runs, so the spread between repeats is visible.
    for dist, runs in by_dist.items():
        ax.plot([dist] * len(runs), [r["pdr"] for r in runs], "x",
                color="#c0504d", markersize=5, alpha=0.75,
                label="individual runs" if dist == min(by_dist) else None)
    ax.axhline(95.0, linestyle=":", linewidth=1.0, color="#777")
    ax.annotate("95 % requirement", xy=(max(xs), 95.0),
                xytext=(0, 5), textcoords="offset points",
                ha="right", fontsize=8, color="#555")
    ax.set_xlabel("Distance (m)")
    ax.set_ylabel("Packet delivery ratio (%)")
    ax.set_title(f"PDR vs distance — {RUN_PACKETS} packets per run")
    ax.set_ylim(-5, 105)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, loc="lower left")
    fig.tight_layout()
    png = ANALYSIS_DIR / "pdr_100pkt.png"
    fig.savefig(png)
    fig.savefig(png.with_suffix(".svg"))
    plt.close(fig)

    print(f"Wrote {png}")
    print(f"Wrote {png.with_suffix('.svg')}")
    print(f"Wrote {out_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
