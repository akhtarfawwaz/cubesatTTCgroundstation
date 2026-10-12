"""analyze_sessions.py - summarise every archived session log.

Usage:
    python analyze_sessions.py [log_dir]            (default: ../range_test_logs)

For each session_*.csv written by the ground station it reports:
  * frames received, frames expected and packet delivery ratio (PDR), with a
    Wilson 95 % confidence interval, so small samples are not over-read;
  * CRC-16 failures (rows whose receiver verdict is FAIL);
  * command activity seen in the downlink 'ack' field: ACK events (a new
    cmd_id appears), NAK events (ack becomes -1) and ACKs that coincide with
    a change of operating mode (evidence that the command executed on board);
  * duration, median telemetry cadence, mean RSSI / SNR and GNSS fix share.

Expected packets are derived from p_id continuity. A row that fails CRC is
excluded from the continuity calculation, because its p_id field is itself
corrupted and would otherwise look like a transmitter reset.
"""
import csv
import glob
import math
import os
import statistics
import sys


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (c - h), 100 * (c + h))


def load(path):
    with open(path, newline="", encoding="utf8", errors="replace") as fh:
        return [r for r in csv.DictReader(fh) if r.get("p_id", "").lstrip("-").isdigit()]


def summarise(path):
    rows = load(path)
    good = [r for r in rows if r["crc"] == "OK"]
    crc_fail = len(rows) - len(good)
    # segments: a genuine transmitter reset makes p_id go backwards
    segs = [[good[0]]] if good else []
    for a, b in zip(good, good[1:]):
        if int(b["p_id"]) < int(a["p_id"]):
            segs.append([])
        segs[-1].append(b)
    expected = sum(int(s[-1]["p_id"]) - int(s[0]["p_id"]) + 1 for s in segs)
    received = len(good)
    duration_h = sum(int(s[-1]["rx_ms"]) - int(s[0]["rx_ms"]) for s in segs) / 3.6e6
    rx = [int(r["rx_ms"]) for r in good]
    cad = [b - a for a, b in zip(rx, rx[1:]) if 0 < b - a < 10000]
    ack_ev = nak_ev = ack_mode = 0
    for a, b in zip(good, good[1:]):
        if b["ack"] != a["ack"]:
            if b["ack"] == "-1":
                nak_ev += 1
            elif b["ack"] != "0":
                ack_ev += 1
                if b["mode"] != a["mode"]:
                    ack_mode += 1
    fix = sum(1 for r in good if r.get("sats", "0") not in ("0", "") and r.get("err", "0") in ("0",))
    lo, hi = wilson(received, expected)
    return dict(
        session=os.path.basename(path)[8:23], received=received, expected=expected,
        pdr=100 * received / expected if expected else float("nan"), ci_lo=lo, ci_hi=hi,
        crc_fail=crc_fail, ack=ack_ev, ack_mode=ack_mode, nak=nak_ev, hours=duration_h,
        cadence=statistics.median(cad) / 1000 if cad else float("nan"),
        rssi=statistics.mean(int(r["rssi"]) for r in good),
        snr=statistics.mean(float(r["snr"]) for r in good),
        fix=100 * fix / received if received else 0,
    )


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    log_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "..", "range_test_logs")
    files = sorted(glob.glob(os.path.join(log_dir, "session_*.csv")))
    out = [summarise(f) for f in files]
    hdr = ("| Session | Received / expected | PDR % (95 % CI) | CRC fail | ACK (mode change) | NAK "
           "| Hours | Cadence s | RSSI dBm | SNR dB | GNSS fix % |")
    print(hdr)
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for s in out:
        print(f"| {s['session']} | {s['received']} / {s['expected']} | {s['pdr']:.2f} ({s['ci_lo']:.1f}-{s['ci_hi']:.1f}) "
              f"| {s['crc_fail']} | {s['ack']} ({s['ack_mode']}) | {s['nak']} | {s['hours']:.2f} | {s['cadence']:.3f} "
              f"| {s['rssi']:.1f} | {s['snr']:.2f} | {s['fix']:.1f} |")
    tr = sum(s["received"] for s in out)
    te = sum(s["expected"] for s in out)
    print(f"\nAll sessions: {tr} / {te} frames, PDR {100 * tr / te:.2f} %, "
          f"CRC failures {sum(s['crc_fail'] for s in out)}, ACK events {sum(s['ack'] for s in out)}, "
          f"NAK events {sum(s['nak'] for s in out)}")


if __name__ == "__main__":
    main()
