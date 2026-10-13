#!/usr/bin/env python3
"""
Command-buffer stress test for the TT&C subsystem.

Fires 20 mode-switch commands at 0.5 s intervals and accounts for every one of
them. The point is the receiver's uplink queue, which holds exactly one command
(queue_uplink() in Receiver/src/main.cpp overwrites up_id/up_ct/up_pm and sets
up_pending) and can only transmit when a downlink arrives, roughly every 2 s.
A 0.5 s command rate against a 2 s transmit opportunity therefore means most
commands are replaced before they are ever sent -- not lost in the radio, not
rejected by the satellite, but superseded on the ground. Expect about 5
TRANSMITTED and about 15 SUPERSEDED per run.

Commands use ct=1 (set mode) with pm alternating 1 and 2, so every executed
command flips the reported `mode` field and execution is visible in telemetry
rather than inferred from the ack alone.

Per command the outcome is one of:
  TRANSMITTED -> then ACK / NAK / TIMEOUT from the following frames
  SUPERSEDED  -> replaced in the single-slot queue before any UPLINK_TX

Whole-run checks: CubeSat reboot (p_id going backwards), frames lost during and
after the burst, CRC failures, any telemetry gap over 6 s, and whether the final
mode matches the pm of the last acknowledged command.

Classification is done after the fact over a timestamped timeline of frames and
receiver comments, rather than live, so the ordering of UPLINK_QUEUED,
UPLINK_TX and the acking frame is judged from what actually happened.

Run:  backend/venv/Scripts/python.exe Receiver/tools/stress_test.py [COM3]
"""

import argparse
import csv
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fault_test import Frame                      # noqa: E402
from range_logger import CSV_FIELDS, LEGACY_FIELD_COUNT, LOG_DIR  # noqa: E402

BAUD = 115200
BURST_N = 20
BURST_INTERVAL = 0.5
SETTLE_S = 30.0          # logging after the last command
GAP_BETWEEN_RUNS = 30.0
ACK_WINDOW_S = 8.0       # matches backend COMMAND_TIMEOUT
MAX_FRAME_GAP_S = 6.0
WARMUP_FRAMES = 5

RE_QUEUED = re.compile(r"UPLINK_QUEUED id=(-?\d+)")
RE_TX = re.compile(r"UPLINK_(TX|FAIL) id=(-?\d+)")


class SerialTransport:
    """Real port. DTR/RTS stay low: they are wired to EN/IO0 on a CH340 ESP32
    and asserting them reboots the receiver."""

    def __init__(self, port, baud=BAUD):
        import serial
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.timeout = 1
        s.dtr = False
        s.rts = False
        s.open()
        time.sleep(0.4)
        s.reset_input_buffer()
        self.s = s

    def read(self, n):
        return self.s.read(n)

    def write(self, b):
        self.s.write(b)
        self.s.flush()

    def close(self):
        try:
            if self.s.is_open:
                self.s.write(b"b")
                time.sleep(0.1)
                self.s.close()
        except Exception:
            pass


class Session:
    """Mirrors everything to a log and records a timestamped timeline.

    `transport` is injectable so the classification logic can be exercised
    against a simulated receiver with no hardware attached.
    """

    def __init__(self, transport, log_path):
        self.tx = transport
        self.log = open(log_path, "w", encoding="utf-8", newline="")
        self.timeline = []          # (t, kind, payload) kind: frame | comment
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _emit(self, direction, text):
        self.log.write("%s %s %s\n"
                       % (datetime.now().isoformat(timespec="milliseconds"),
                          direction, text))
        self.log.flush()

    def _read(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self.tx.read(256)
            except Exception as exc:
                self._emit("ERR", "read failed: %s" % exc)
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._emit("RX ", line)
                now = time.time()
                if line.startswith("#"):
                    with self._lock:
                        self.timeline.append((now, "comment", line))
                    continue
                if line.startswith("rx_ms,"):
                    continue
                parts = line.split(",")
                if len(parts) not in (len(CSV_FIELDS), LEGACY_FIELD_COUNT):
                    continue
                try:
                    f = Frame(parts, line)
                except (ValueError, IndexError):
                    continue
                with self._lock:
                    self.timeline.append((now, "frame", f))

    def send(self, text):
        self._emit("TX ", text)
        self.tx.write((text + "\n").encode())

    def send_key(self, key):
        self._emit("TX ", "<key %r>" % key)
        self.tx.write(key.encode())

    def snapshot(self):
        with self._lock:
            return list(self.timeline)

    def frames_since(self, idx):
        return [e for e in self.snapshot()[idx:] if e[1] == "frame"]

    def wait_frames(self, n, timeout):
        """Block until n frames have arrived in total. Returns them."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            fr = [e for e in self.snapshot() if e[1] == "frame"]
            if len(fr) >= n:
                return fr
            time.sleep(0.1)
        return [e for e in self.snapshot() if e[1] == "frame"]

    def close(self):
        self._stop.set()
        self.t.join(timeout=2)
        self.tx.close()
        self.log.close()


def classify(timeline, sent):
    """Account for every command id from the timeline.

    sent: list of (id, pm, pc_time_sent)
    """
    # Index the comment lines once.
    queued, txd = {}, {}
    for i, (t, kind, payload) in enumerate(timeline):
        if kind != "comment":
            continue
        m = RE_QUEUED.search(payload)
        if m:
            queued.setdefault(int(m.group(1)), (i, t))
        m = RE_TX.search(payload)
        if m:
            txd.setdefault(int(m.group(2)), (i, t, m.group(1)))

    rows = []
    for cid, pm, pc_sent in sent:
        row = {"id": cid, "pm": pm, "pc_time_sent": pc_sent,
               "outcome": "", "response_pid": "", "frames_after_tx": "",
               "mode_after": "", "detail": ""}
        if cid not in txd:
            row["outcome"] = "SUPERSEDED"
            row["detail"] = ("queued but never transmitted; the single-slot "
                             "queue was overwritten by a later command")
            if cid not in queued:
                row["detail"] = "no UPLINK_QUEUED seen at all"
                row["outcome"] = "NOT_QUEUED"
            rows.append(row)
            continue

        tx_idx, tx_t, kind = txd[cid]
        if kind == "FAIL":
            row["outcome"] = "TX_FAIL"
            row["detail"] = "LoRa.endPacket() reported failure"
            rows.append(row)
            continue

        n = 0
        for (t, k, payload) in timeline[tx_idx + 1:]:
            if k != "frame":
                continue
            n += 1
            if t - tx_t > ACK_WINDOW_S:
                break
            if payload.ack == cid:
                row.update(outcome="ACK", response_pid=payload.p_id,
                           frames_after_tx=n, mode_after=payload.mode)
                row["detail"] = ("mode %s pm" % ("matches" if payload.mode == pm
                                                 else "DOES NOT match"))
                break
            if payload.ack == -1:
                row.update(outcome="NAK", response_pid=payload.p_id,
                           frames_after_tx=n, mode_after=payload.mode)
                break
        if not row["outcome"]:
            row["outcome"] = "TIMEOUT"
            row["detail"] = "no ack within %.0f s of UPLINK_TX" % ACK_WINDOW_S
        rows.append(row)
    return rows


def run_checks(timeline, burst_t0, burst_t1):
    frames = [(t, f) for (t, k, f) in timeline if k == "frame"]
    resets, crc_fail = 0, 0
    lost_before, lost_burst, lost_after, max_gap = 0, 0, 0, 0.0
    prev_t = prev_pid = None
    for t, f in frames:
        if not f.crc_ok:
            crc_fail += 1
        if prev_pid is not None:
            if f.p_id < prev_pid:
                # Reboot: the p_id sequence no longer describes what was sent,
                # so the difference is not a loss count.
                resets += 1
            else:
                gap = f.p_id - prev_pid - 1
                if gap > 0:
                    # Attribute by when the frame arrived. Warm-up losses are
                    # not burst losses; lumping them in overstated the effect
                    # of the command burst on the downlink.
                    if t < burst_t0:
                        lost_before += gap
                    elif t <= burst_t1:
                        lost_burst += gap
                    else:
                        lost_after += gap
            max_gap = max(max_gap, t - prev_t)
        prev_t, prev_pid = t, f.p_id
    return {
        "frames": len(frames),
        "resets": resets,
        "crc_fail": crc_fail,
        "lost_before_burst": lost_before,
        "lost_during_burst": lost_burst,
        "lost_after_burst": lost_after,
        "max_frame_gap_s": round(max_gap, 2),
        "continuous": "yes" if max_gap <= MAX_FRAME_GAP_S else "NO",
        "final_mode": frames[-1][1].mode if frames else "",
    }


def one_run(sess, run_no, base):
    print("\n=== run %d  (ids %d..%d) ===" % (run_no, base + 1, base + BURST_N))
    start_idx = len(sess.snapshot())
    fr = sess.wait_frames(len([e for e in sess.snapshot() if e[1] == "frame"])
                          + WARMUP_FRAMES, 40)
    if len(fr) < WARMUP_FRAMES:
        print("   only %d frames before the burst — link may be down" % len(fr))
    base_pid = fr[-1][2].p_id if fr else None
    base_mode = fr[-1][2].mode if fr else None
    print("   start p_id=%s mode=%s" % (base_pid, base_mode))

    sent = []
    burst_t0 = time.time()
    for k in range(BURST_N):
        cid = base + 1 + k
        # pm cycles with period 3, NOT 2. Only every 4th command survives the
        # single-slot queue, so a period-2 pattern lands on the same parity
        # every time: the first real run transmitted ids ending 2,6,10,14,18
        # and every one of them carried pm=2, so the mode never actually
        # changed and the final-mode check passed trivially. 3 is coprime with
        # the stride of 4, so the surviving commands carry mixed pm.
        pm = 2 if k % 3 == 2 else 1
        sess.send(">%d,1,%d" % (cid, pm))
        sent.append((cid, pm, datetime.now().isoformat(timespec="milliseconds")))
        time.sleep(BURST_INTERVAL)
    burst_t1 = time.time()
    print("   %d commands sent in %.1f s; logging %.0f s more"
          % (BURST_N, burst_t1 - burst_t0, SETTLE_S))
    time.sleep(SETTLE_S)

    tl = sess.snapshot()[start_idx:]
    rows = classify(tl, sent)
    checks = run_checks(tl, burst_t0, burst_t1)

    counts = {}
    for r in rows:
        counts[r["outcome"]] = counts.get(r["outcome"], 0) + 1
    print("   " + "  ".join("%s=%d" % kv for kv in sorted(counts.items())))
    print("   frames=%d  lost(before/burst/after)=%d/%d/%d  crc_fail=%d  "
          "resets=%d  max_gap=%.1fs  continuous=%s"
          % (checks["frames"], checks["lost_before_burst"],
             checks["lost_during_burst"], checks["lost_after_burst"],
             checks["crc_fail"], checks["resets"],
             checks["max_frame_gap_s"], checks["continuous"]))

    acked = [r for r in rows if r["outcome"] == "ACK"]
    if acked:
        last = acked[-1]
        ok = checks["final_mode"] == last["pm"]
        checks["final_mode_matches_last_ack"] = "yes" if ok else "NO"
        print("   last ACK id=%s pm=%s ; final mode=%s -> %s"
              % (last["id"], last["pm"], checks["final_mode"],
                 "match" if ok else "MISMATCH"))
    else:
        checks["final_mode_matches_last_ack"] = "n/a"
        print("   no command was acknowledged in this run")

    for r in rows:
        r["run"] = run_no
    return rows, counts, checks


def main(argv=None, transport=None):
    """`transport` is a test hook: pass a simulated receiver to exercise the
    classification logic with no hardware attached."""
    ap = argparse.ArgumentParser(description="TT&C command-buffer stress test")
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args(argv)

    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / ("stress_test_%s.log" % stamp)
    csv_path = LOG_DIR / ("stress_test_%s.csv" % stamp)

    print("stress_test  port=%s  runs=%d  burst=%d every %.1fs"
          % (args.port, args.runs, BURST_N, BURST_INTERVAL))
    try:
        transport = transport or SerialTransport(args.port)
    except Exception as exc:
        print("could not open %s: %s" % (args.port, exc))
        print("Close the PlatformIO monitor, the backend and range_logger "
              "first — only one process may hold the port.")
        return 2

    sess = Session(transport, log_path)
    all_rows, summaries = [], []
    try:
        sess.send_key("c")
        time.sleep(0.5)
        for i in range(args.runs):
            rows, counts, checks = one_run(sess, i + 1, 2000 + i * 100)
            all_rows += rows
            summaries.append((i + 1, counts, checks))
            if i + 1 < args.runs:
                print("   waiting %.0f s before the next run" % GAP_BETWEEN_RUNS)
                time.sleep(GAP_BETWEEN_RUNS)
    except KeyboardInterrupt:
        print("\ninterrupted — saving what was collected")
    finally:
        sess.close()

    if not all_rows:
        print("no results")
        return 1

    cols = ["run", "id", "pm", "pc_time_sent", "outcome", "response_pid",
            "frames_after_tx", "mode_after", "detail"]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in all_rows:
            w.writerow({k: r.get(k, "") for k in cols})

    print("\n\n## Command-buffer stress test\n")
    print("| Run | ACK | NAK | TIMEOUT | SUPERSEDED | Other | Frames | "
          "Lost before | Lost burst | Lost after | CRC fail | Resets | "
          "Max gap s | Continuous | Final mode = last ACK |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for run_no, counts, c in summaries:
        other = sum(v for k, v in counts.items()
                    if k not in ("ACK", "NAK", "TIMEOUT", "SUPERSEDED"))
        print("| %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | "
              "%.1f | %s | %s |"
              % (run_no, counts.get("ACK", 0), counts.get("NAK", 0),
                 counts.get("TIMEOUT", 0), counts.get("SUPERSEDED", 0), other,
                 c["frames"], c["lost_before_burst"], c["lost_during_burst"],
                 c["lost_after_burst"], c["crc_fail"], c["resets"],
                 c["max_frame_gap_s"], c["continuous"],
                 c["final_mode_matches_last_ack"]))

    tot = {}
    for r in all_rows:
        tot[r["outcome"]] = tot.get(r["outcome"], 0) + 1
    print("\nAll runs: " + "  ".join("%s=%d" % kv for kv in sorted(tot.items()))
          + "  (of %d commands)" % len(all_rows))
    print("\nraw log -> %s" % log_path)
    print("results -> %s" % csv_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
