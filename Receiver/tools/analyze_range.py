#!/usr/bin/env python3
"""
Offline analysis of the TT&C range-test logs.

Replays each archived CSV through the same Stats class the live logger uses, so
the numbers that end up in the manuscript carry exactly the definition of PDR
documented in range_logger.py -- derived from p_id continuity, not from the
receiver's own counters.

Produces, into range_test_logs/analysis/:
  table_5_2.md / .csv    Table 5.2, per-distance link summary
  fig_5_6_pdr.png/.svg   Figure 5.6, PDR vs distance
  fig_5_7_rssi_snr.*     Figure 5.7, RSSI and SNR vs distance
and regenerates range_test_logs/summary.csv, which the live tool appends to but
which is missing from the archived runs.

Run:  backend/venv/Scripts/python.exe Receiver/tools/analyze_range.py
"""

import csv
import re
import statistics as st
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")           # no display on this box; write files only
import matplotlib.pyplot as plt

from range_logger import CSV_FIELDS, SUMMARY_FIELDS, LOG_DIR, Stats

OUT_DIR = LOG_DIR / "analysis"

# SX1276/78 datasheet sensitivity at SF7 / 125 kHz. Drawn on Figure 5.7 so the
# reader can see how much margin each measurement point actually had.
SF7_SENSITIVITY_DBM = -123.0

# Table 4.9 targets, as revised against the measured envelope.
PDR_REQUIREMENT_PCT = 95.0


def distance_from_name(path):
    """Pull the distance out of the filename.

    Tolerates both the logger's own `run_10m_<stamp>.csv` and the hand-renamed
    `10m data.csv` files that the archived runs were saved as.
    """
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", path.stem, re.IGNORECASE)
    return float(m.group(1)) if m else None


def analyse(path):
    """Replay one CSV and return its link statistics."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r.get("p_id")]
    if not rows:
        return None

    stats = Stats()
    rx_ms, pids = [], []
    for r in rows:
        try:
            pid = int(r["p_id"])
            stats.add_packet(pid, int(r["rssi"]), float(r["snr"]), r["crc"] == "OK")
            pids.append(pid)
            rx_ms.append(int(r["rx_ms"]))
        except (ValueError, KeyError):
            continue

    # Stats.duration and Stats.cadence are wall-clock based and meaningless for
    # a replay, so recover both from the receiver's own rx_ms timestamps.
    #
    # Two cadence figures, because they answer different questions: the mean over
    # ALL arrivals is inflated wherever a frame was lost, while the mean over
    # pairs whose p_id increments by exactly 1 is the transmitter's true period.
    # The requirement is about the transmit period, so that is the headline.
    deltas_all, deltas_adj = [], []
    for i in range(len(rx_ms) - 1):
        d = (rx_ms[i + 1] - rx_ms[i]) / 1000.0
        deltas_all.append(d)
        if pids[i + 1] == pids[i] + 1:
            deltas_adj.append(d)

    return {
        "path": path,
        "distance_m": distance_from_name(path),
        "stats": stats,
        "duration_s": (rx_ms[-1] - rx_ms[0]) / 1000.0 if len(rx_ms) > 1 else 0.0,
        "cadence_s": st.mean(deltas_adj) if deltas_adj else 0.0,
        "cadence_all_s": st.mean(deltas_all) if deltas_all else 0.0,
        "gaps": list(stats.gaps),
    }


def fmt_gaps(gaps):
    if not gaps:
        return "none"
    return "; ".join(f"{a}–{b}" if b > a else str(a) for a, b in gaps)


def write_table(results):
    """Table 5.2, as Markdown for review and CSV for pasting into Word."""
    header = [
        "Distance (m)", "p_id span", "Expected", "Received", "PDR (%)",
        "Lost", "CRC fail", "RSSI avg (dBm)", "RSSI min/max (dBm)",
        "SNR avg (dB)", "SNR min/max (dB)", "Cadence (s)", "Duration (s)",
    ]
    rows = []
    for r in results:
        s = r["stats"]
        rows.append([
            f"{r['distance_m']:g}",
            f"{s.first_pid}–{s.last_pid}",
            str(s.expected),
            str(s.arrived),
            f"{s.pdr:.2f}",
            str(s.lost),
            str(s.crc_fail),
            f"{st.mean(s.rssi):.1f}" if s.rssi else "—",
            f"{min(s.rssi)} / {max(s.rssi)}" if s.rssi else "—",
            f"{st.mean(s.snr):+.2f}" if s.snr else "—",
            f"{min(s.snr):+.1f} / {max(s.snr):+.1f}" if s.snr else "—",
            f"{r['cadence_s']:.3f}",
            f"{r['duration_s']:.1f}",
        ])

    md = OUT_DIR / "table_5_2.md"
    with open(md, "w", encoding="utf-8") as fh:
        fh.write("Table 5.2 LoRa Link Range Characterization "
                 "(433 MHz, SF7, 125 kHz, CR 4/5, 17 dBm, outdoor LOS)\n\n")
        fh.write("| " + " | ".join(header) + " |\n")
        fh.write("|" + "|".join(["---"] * len(header)) + "|\n")
        for row in rows:
            fh.write("| " + " | ".join(row) + " |\n")
        fh.write("\nLost-frame p_id ranges: "
                 + "; ".join(f"{r['distance_m']:g} m: {fmt_gaps(r['gaps'])}"
                             for r in results) + "\n")

    with open(OUT_DIR / "table_5_2.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)

    return md


def write_summary(results):
    """Rebuild the summary.csv the live logger would have appended per run.

    The archived CSVs were renamed by hand and the original run timestamps are
    gone, so the timestamp column carries the file's mtime and the note says so
    rather than inventing a plausible-looking clock time.
    """
    out = LOG_DIR / "summary.csv"
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
        w.writeheader()
        for r in results:
            s = r["stats"]
            mtime = datetime.fromtimestamp(r["path"].stat().st_mtime)
            w.writerow({
                "timestamp": mtime.isoformat(timespec="seconds"),
                "distance_m": f"{r['distance_m']:g}",
                "notes": f"LOS; rebuilt offline from {r['path'].name} "
                         f"(timestamp = file mtime, original run clock not retained)",
                "duration_s": round(r["duration_s"], 1),
                "pdr_pct": round(s.pdr, 2),
                "usable_pct": round(s.usable, 2),
                "good": s.good,
                "lost": s.lost,
                "crc_fail": s.crc_fail,
                # Parse failures were counted from '#' log lines the receiver
                # printed live; those never reached the CSV, so replay cannot
                # recover them. Reported as 0, not as unknown.
                "parse_fail": 0,
                "expected": s.expected,
                "arrived": s.arrived,
                "first_pid": s.first_pid,
                "last_pid": s.last_pid,
                "rssi_avg": round(st.mean(s.rssi), 1) if s.rssi else "",
                "rssi_min": min(s.rssi) if s.rssi else "",
                "rssi_max": max(s.rssi) if s.rssi else "",
                "snr_avg": round(st.mean(s.snr), 2) if s.snr else "",
                "snr_min": min(s.snr) if s.snr else "",
                "snr_max": max(s.snr) if s.snr else "",
                "cadence_s": round(r["cadence_s"], 3),
            })
    return out


def _save(fig, stem):
    for ext in ("png", "svg"):
        fig.savefig(OUT_DIR / f"{stem}.{ext}", dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_pdr(results):
    """Figure 5.6 -- PDR vs distance against the 95 % requirement."""
    d = [r["distance_m"] for r in results]
    pdr = [r["stats"].pdr for r in results]

    fig, ax = plt.subplots(figsize=(6.0, 3.8))
    ax.plot(d, pdr, marker="o", color="#1a1a1a", linewidth=1.6,
            markersize=7, zorder=3, label="Measured PDR")
    ax.axhline(PDR_REQUIREMENT_PCT, color="#666", linestyle="--", linewidth=1.2,
               zorder=2, label=f"Requirement ({PDR_REQUIREMENT_PCT:g} %)")
    ax.fill_between([min(d) - 5, max(d) + 5], PDR_REQUIREMENT_PCT, 100,
                    color="#000", alpha=0.05, zorder=1)

    for x, y in zip(d, pdr):
        ax.annotate(f"{y:.1f} %", (x, y), textcoords="offset points",
                    xytext=(0, -16), ha="center", fontsize=9)

    ax.set_xlabel("Distance (m), outdoor line-of-sight")
    ax.set_ylabel("Packet delivery ratio (%)")
    ax.set_xlim(min(d) - 5, max(d) + 5)
    ax.set_ylim(0, 105)
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)
    ax.legend(frameon=False, fontsize=9, loc="lower left")
    _save(fig, "fig_5_6_pdr")


def plot_rssi_snr(results):
    """Figure 5.7 -- RSSI and SNR vs distance, with the SF7 sensitivity floor.

    Markers and linestyles differ as well as colour: theses get printed in
    greyscale, where a colour-only distinction disappears.
    """
    d = [r["distance_m"] for r in results]
    rssi = [st.mean(r["stats"].rssi) for r in results]
    snr = [st.mean(r["stats"].snr) for r in results]
    rssi_err = [[m - min(r["stats"].rssi) for m, r in zip(rssi, results)],
                [max(r["stats"].rssi) - m for m, r in zip(rssi, results)]]
    snr_err = [[m - min(r["stats"].snr) for m, r in zip(snr, results)],
               [max(r["stats"].snr) - m for m, r in zip(snr, results)]]

    fig, ax = plt.subplots(figsize=(6.4, 3.9))
    ax.errorbar(d, rssi, yerr=rssi_err, marker="o", color="#1a1a1a",
                linewidth=1.6, markersize=7, capsize=4, zorder=3,
                label="RSSI (mean, min–max)")
    ax.axhline(SF7_SENSITIVITY_DBM, color="#666", linestyle="--", linewidth=1.2,
               zorder=2, label=f"SF7/125 kHz sensitivity ({SF7_SENSITIVITY_DBM:g} dBm)")
    ax.set_xlabel("Distance (m), outdoor line-of-sight")
    ax.set_ylabel("RSSI (dBm)")
    ax.set_xlim(min(d) - 5, max(d) + 5)
    ax.grid(True, linestyle=":", linewidth=0.6, alpha=0.6)

    ax2 = ax.twinx()
    ax2.errorbar(d, snr, yerr=snr_err, marker="s", color="#777",
                 linestyle="-.", linewidth=1.6, markersize=6, capsize=4,
                 zorder=3, label="SNR (mean, min–max)")
    ax2.axhline(0, color="#bbb", linewidth=0.8, zorder=1)
    ax2.set_ylabel("SNR (dB)")

    # Both series descend left-to-right across the whole plot area, so an inset
    # legend lands on top of the data at any anchor. Put it under the axes.
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, frameon=False, fontsize=8.5,
              loc="upper center", bbox_to_anchor=(0.5, -0.20), ncol=3,
              columnspacing=1.4, handlelength=2.6)
    _save(fig, "fig_5_7_rssi_snr")


def main():
    files = sorted(
        (p for p in LOG_DIR.glob("*.csv") if p.name != "summary.csv"),
        key=lambda p: distance_from_name(p) or 0,
    )
    results = []
    for p in files:
        if distance_from_name(p) is None:
            print(f"  skipped {p.name}: no distance in filename")
            continue
        r = analyse(p)
        if r:
            results.append(r)

    if not results:
        print(f"No usable range logs found in {LOG_DIR}")
        return 1

    OUT_DIR.mkdir(exist_ok=True)

    print(f"\n{'dist':>6}  {'PDR':>8}  {'good/exp':>9}  {'RSSI':>8}  "
          f"{'SNR':>7}  {'cadence':>8}")
    print("  " + "-" * 56)
    for r in results:
        s = r["stats"]
        dist = f"{r['distance_m']:g}m".rjust(6)
        print(f"{dist}  {s.pdr:>7.2f}%  "
              f"{s.good:>4}/{s.expected:<4}  {st.mean(s.rssi):>7.1f}  "
              f"{st.mean(s.snr):>+6.2f}  {r['cadence_s']:>7.3f}s")

    table = write_table(results)
    summary = write_summary(results)
    plot_pdr(results)
    plot_rssi_snr(results)

    print(f"\n  table   -> {table.relative_to(LOG_DIR.parent)}")
    print(f"  summary -> {summary.relative_to(LOG_DIR.parent)}")
    print(f"  figures -> {OUT_DIR.relative_to(LOG_DIR.parent)}/"
          f"fig_5_6_pdr.[png|svg], fig_5_7_rssi_snr.[png|svg]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
