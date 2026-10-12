#!/usr/bin/env python3
"""
TT&C LoRa range-test logger.

Two modes:

  Auto-stop at 100 packets (default)
      Enter distance, run number and notes, press ARM, then power on the
      CubeSat at the measurement point. Logging begins on the first CRC-OK
      frame. The run ends when the last packet of the 100-packet window
      arrives, or 230 s after arming, whichever comes first. Each run writes
      run_{distance}m_r{run}_{stamp}.csv and appends one row to
      summary_100pkt.csv. A run in which nothing arrives is still saved, with
      received 0 / expected 100 / PDR 0.

  Free-running (checkbox off)
      The original behaviour: START / STOP by hand, PDR derived from observed
      p_id continuity, one row appended to summary.csv.

PDR is computed here from the p_id sequence rather than trusting the receiver's
own counters, so a mid-run board reset can't silently corrupt a measurement.

The 100-packet window is anchored on the FIRST p_id actually received, not on a
hardcoded 0. The firmware declares `uint32_t p_id = 1` (Receiver/src/main.cpp)
and increments after each transmission, so a freshly booted CubeSat sends
1..100 as its first hundred packets. Anchoring on the first observation means
the window is right whether the board starts at 1, starts at 0 after a firmware
change, or was already running when the run was armed.

Run:  backend/venv/Scripts/python.exe Receiver/tools/range_logger.py
"""

import csv
import math
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk

import serial
import serial.tools.list_ports

BAUD = 115200
LOG_DIR = Path(__file__).resolve().parent.parent / "range_test_logs"

# --- 100-packet run parameters -------------------------------------------
RUN_PACKETS = 100          # transmitted packets that define one run
TX_PERIOD_S = 2.0          # TELEMETRY_INTERVAL_MS 2000 in the firmware
ARM_GRACE_S = 30.0         # slack for end-of-run losses and no-show runs
ARM_TIMEOUT_S = RUN_PACKETS * TX_PERIOD_S + ARM_GRACE_S    # 230 s
BOOT_PID = 1               # firmware: uint32_t p_id = 1, incremented after TX
FRESH_BOOT_MAX = 5         # first p_id above this => board was already running

# Wire format printed by the receiver. Used both to validate incoming row
# widths and as the raw-CSV column order.
CSV_FIELDS = [
    "rx_ms", "p_id", "rssi", "snr", "crc", "ts", "mode", "temp", "press", "alt",
    "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw",
    "lat", "lon", "gps_alt", "sats", "volt", "err", "ack",
    "alt_kf",
]

# The Kalman-fused altitude was appended after the 10 m / 50 m runs were
# captured, so both row widths have to be accepted or the archived logs stop
# replaying against this tool.
LEGACY_FIELD_COUNT = len(CSV_FIELDS) - 1

# What gets written to disk: the wire columns plus the PC's own clock, so the
# run date survives even though the firmware's `ts` is not a wall clock before
# a GNSS fix.
OUT_FIELDS = CSV_FIELDS + ["pc_time"]

SUMMARY_FIELDS = [
    "timestamp", "distance_m", "notes", "duration_s",
    "pdr_pct", "usable_pct", "good", "lost", "crc_fail", "parse_fail",
    "expected", "arrived", "first_pid", "last_pid",
    "rssi_avg", "rssi_min", "rssi_max", "snr_avg", "snr_min", "snr_max",
    "cadence_s",
]

# Separate file: the column set differs from the legacy summary, and the old
# rows must stay readable by analyze_range.py.
SUMMARY_100_FIELDS = [
    "timestamp", "distance_m", "run", "notes",
    "received", "expected", "lost_pids", "crc_fail",
    "pdr_pct", "ci_low_pct", "ci_high_pct",
    "rssi_mean", "rssi_min", "rssi_max",
    "snr_mean", "snr_min", "snr_max",
    "cadence_s", "duration_s",
    "window_start", "window_end", "first_pid_seen", "fresh_boot", "stop_reason",
]


def wilson_ci(k, n, z=1.96):
    """Wilson score interval for a binomial proportion, in percent.

    Used instead of the normal approximation because these runs sit at the
    extremes -- 100/100 and 0/100 both occur -- where the normal interval
    collapses to zero width and is simply wrong.
    """
    if n <= 0:
        return 0.0, 0.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - half) * 100.0), min(100.0, (centre + half) * 100.0)


class Stats:
    """Link statistics for one free-running point, from p_id continuity."""

    def __init__(self):
        self.first_pid = None
        self.last_pid = None
        self.good = 0
        self.crc_fail = 0
        self.parse_fail = 0
        self.rssi = []
        self.snr = []
        self.gaps = []          # (from_pid, to_pid)
        self.started = time.time()

    def add_packet(self, pid, rssi, snr, crc_ok):
        if self.first_pid is None:
            self.first_pid = pid
        elif pid > self.last_pid + 1:
            self.gaps.append((self.last_pid + 1, pid - 1))
        elif pid < self.last_pid:
            # Transmitter rebooted mid-run. The p_id range no longer describes
            # what was sent, so this point has to be redone -- say so loudly
            # rather than reporting a PDR computed from a broken sequence.
            self.first_pid = pid
            self.gaps.clear()
            self.good = self.crc_fail = 0
            self.rssi.clear()
            self.snr.clear()
            self.started = time.time()
        self.last_pid = pid
        if crc_ok:
            self.good += 1
            self.rssi.append(rssi)
            self.snr.append(snr)
        else:
            self.crc_fail += 1

    @property
    def expected(self):
        if self.first_pid is None:
            return 0
        return self.last_pid - self.first_pid + 1

    @property
    def arrived(self):
        return self.good + self.crc_fail + self.parse_fail

    @property
    def lost(self):
        return max(0, self.expected - (self.good + self.crc_fail))

    @property
    def pdr(self):
        return 100.0 * self.good / self.expected if self.expected else 0.0

    @property
    def usable(self):
        return 100.0 * self.good / self.arrived if self.arrived else 0.0

    @property
    def duration(self):
        return time.time() - self.started

    @property
    def cadence(self):
        return self.duration / (self.good - 1) if self.good > 1 else 0.0


class Run100:
    """One fixed-size run: a window of RUN_PACKETS consecutive p_id values.

    Expected is RUN_PACKETS by definition, never derived from what arrived, so
    a run that receives nothing still reports 0 / 100 rather than 0 / 0.
    """

    def __init__(self, packets=RUN_PACKETS):
        self.packets = packets
        self.armed_at = time.time()
        self.window_start = None
        self.first_pid_seen = None
        self.fresh_boot = True
        self.ok_pids = set()
        self.crc_fail = 0
        self.parse_fail = 0
        self.rssi = []
        self.snr = []
        self.first_rx_ms = None
        self.last_rx_ms = None
        self.stop_reason = ""

    # -- window ----------------------------------------------------------
    def _anchor(self, pid):
        self.first_pid_seen = pid
        if pid <= FRESH_BOOT_MAX:
            # Freshly powered: anchor on the firmware's boot value so a lost
            # first packet is counted as lost rather than silently excluded.
            self.window_start = BOOT_PID
            self.fresh_boot = True
        else:
            self.window_start = pid
            self.fresh_boot = False

    @property
    def window_end(self):
        if self.window_start is None:
            return None
        return self.window_start + self.packets - 1

    @property
    def anchored(self):
        return self.window_start is not None

    # -- ingest ----------------------------------------------------------
    def add(self, pid, rssi, snr, crc_ok, rx_ms):
        """Returns True if this frame closes the run."""
        if not self.anchored:
            self._anchor(pid)
        in_window = self.window_start <= pid <= self.window_end
        if crc_ok:
            if in_window:
                self.ok_pids.add(pid)
                self.rssi.append(rssi)
                self.snr.append(snr)
                if self.first_rx_ms is None:
                    self.first_rx_ms = rx_ms
                self.last_rx_ms = rx_ms
        elif in_window:
            # Logged to the raw CSV by the caller, but never counted as received.
            self.crc_fail += 1
        return pid >= self.window_end

    # -- results ---------------------------------------------------------
    @property
    def received(self):
        return len(self.ok_pids)

    @property
    def expected(self):
        return self.packets

    @property
    def pdr(self):
        return 100.0 * self.received / self.packets

    @property
    def ci(self):
        return wilson_ci(self.received, self.packets)

    @property
    def lost_pids(self):
        if not self.anchored:
            return list(range(BOOT_PID, BOOT_PID + self.packets))
        return [p for p in range(self.window_start, self.window_end + 1)
                if p not in self.ok_pids]

    @property
    def duration(self):
        """Reception span, first to last counted frame. 0 if nothing arrived."""
        if self.first_rx_ms is None or self.last_rx_ms is None:
            return 0.0
        return (self.last_rx_ms - self.first_rx_ms) / 1000.0

    @property
    def cadence(self):
        if self.received < 2:
            return 0.0
        return self.duration / (self.received - 1)

    @property
    def armed_elapsed(self):
        return time.time() - self.armed_at

    def timed_out(self):
        return self.armed_elapsed >= ARM_TIMEOUT_S


class SerialReader(threading.Thread):
    """Owns the port; pushes decoded lines onto a queue."""

    def __init__(self, port, out_q):
        super().__init__(daemon=True)
        self.port = port
        self.out_q = out_q
        self.ser = None
        self._stop = threading.Event()

    def run(self):
        try:
            # Leave DTR/RTS deasserted: on a CH340-based ESP32 those lines are
            # wired to EN/IO0, and asserting them reboots the board -- which
            # would wipe the receiver's stats the instant we connect.
            self.ser = serial.Serial()
            self.ser.port = self.port
            self.ser.baudrate = BAUD
            self.ser.timeout = 1
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.open()
        except Exception as exc:
            self.out_q.put(("error", f"Could not open {self.port}: {exc}"))
            return

        self.out_q.put(("status", f"connected to {self.port}"))
        time.sleep(0.4)
        self.ser.reset_input_buffer()
        self.ser.write(b"c")      # force CSV mode (idempotent)
        time.sleep(0.2)
        self.ser.write(b"r")      # reset the receiver's own counters too
        self.out_q.put(("status", "ready"))

        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                self.out_q.put(("error", f"serial read failed: {exc}"))
                break
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self.out_q.put(("line", raw.decode("utf-8", "replace").strip()))

        try:
            if self.ser and self.ser.is_open:
                self.ser.write(b"b")   # hand the board back in readable mode
                time.sleep(0.1)
                self.ser.close()
        except Exception:
            pass
        self.out_q.put(("status", "disconnected"))

    def stop(self):
        self._stop.set()


class App:
    def __init__(self, root):
        self.root = root
        root.title("TT&C Range Test Logger")
        root.minsize(820, 620)

        self.reader = None
        self.q = queue.Queue()
        self.stats = None          # free-running mode
        self.run100 = None         # 100-packet mode
        self.rows = []
        self.run_path = None
        self.armed = False
        self.capturing = False

        self._build_ui()
        LOG_DIR.mkdir(exist_ok=True)
        self.root.after(100, self._pump)

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}

        top = ttk.LabelFrame(self.root, text="Measurement point")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Port").grid(row=0, column=0, sticky="w", **pad)
        self.port_cb = ttk.Combobox(top, width=12, values=self._ports())
        self.port_cb.grid(row=0, column=1, sticky="w", **pad)
        ports = self._ports()
        self.port_cb.set("COM3" if "COM3" in ports else (ports[0] if ports else ""))

        ttk.Button(top, text="Refresh", width=8,
                   command=lambda: self.port_cb.configure(values=self._ports())
                   ).grid(row=0, column=2, sticky="w", **pad)

        ttk.Label(top, text="Distance (m)").grid(row=0, column=3, sticky="e", **pad)
        self.dist_var = tk.StringVar(value="10")
        self.dist_e = ttk.Entry(top, width=8, textvariable=self.dist_var)
        self.dist_e.grid(row=0, column=4, sticky="w", **pad)

        ttk.Label(top, text="Run #").grid(row=0, column=5, sticky="e", **pad)
        self.run_var = tk.StringVar(value="1")
        self.run_e = ttk.Entry(top, width=6, textvariable=self.run_var)
        self.run_e.grid(row=0, column=6, sticky="w", **pad)

        ttk.Label(top, text="Notes").grid(row=1, column=0, sticky="w", **pad)
        self.notes_var = tk.StringVar(
            value="LOS; antenna height ? m; weather ?; antennas ?")
        ttk.Entry(top, textvariable=self.notes_var, width=56).grid(
            row=1, column=1, columnspan=6, sticky="we", **pad)

        self.auto100 = tk.BooleanVar(value=True)
        self.auto_cb = ttk.Checkbutton(
            top, text=f"Auto-stop at {RUN_PACKETS} packets",
            variable=self.auto100, command=self._mode_changed)
        self.auto_cb.grid(row=2, column=1, columnspan=3, sticky="w", **pad)

        self.hint = ttk.Label(top, text="", foreground="#0a7")
        self.hint.grid(row=2, column=4, columnspan=3, sticky="w", **pad)

        self.btn = ttk.Button(top, text="ARM", width=14, command=self._toggle)
        self.btn.grid(row=0, column=7, rowspan=3, sticky="nsew", **pad)
        top.columnconfigure(4, weight=1)

        # live stats
        live = ttk.LabelFrame(self.root, text="Live")
        live.pack(fill="x", **pad)
        self.vars = {}
        cells = [
            ("PDR", "pdr", 0, 0), ("Received / expected", "recv", 0, 1),
            ("95 % CI", "ci", 0, 2), ("Lost", "lost", 0, 3),
            ("CRC fail", "crc", 1, 0), ("Parse fail", "parse", 1, 1),
            ("RSSI avg", "rssi", 1, 2), ("SNR avg", "snr", 1, 3),
            ("Elapsed", "elapsed", 2, 0), ("Cadence", "cadence", 2, 1),
            ("Window", "range", 2, 2), ("State", "gaps", 2, 3),
        ]
        for label, key, r, c in cells:
            f = ttk.Frame(live)
            f.grid(row=r, column=c, sticky="w", padx=10, pady=3)
            ttk.Label(f, text=label, foreground="#666").pack(anchor="w")
            v = tk.StringVar(value="—")
            self.vars[key] = v
            ttk.Label(f, textvariable=v, font=("Consolas", 13, "bold")).pack(anchor="w")
        for c in range(4):
            live.columnconfigure(c, weight=1)

        # log
        logf = ttk.LabelFrame(self.root, text="Serial")
        logf.pack(fill="both", expand=True, **pad)
        self.txt = tk.Text(logf, height=12, font=("Consolas", 9),
                           bg="#111", fg="#ddd", insertbackground="#ddd")
        self.txt.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, command=self.txt.yview)
        sb.pack(side="right", fill="y")
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.tag_config("warn", foreground="#ffb454")
        self.txt.tag_config("err", foreground="#ff6b6b")
        self.txt.tag_config("info", foreground="#6bcbff")
        self.txt.tag_config("ok", foreground="#7ddf7d")

        self.status = tk.StringVar(value="idle")
        ttk.Label(self.root, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")
        self._mode_changed()

    @staticmethod
    def _ports():
        return [p.device for p in serial.tools.list_ports.comports()]

    def _mode_changed(self):
        if self.reader:
            return
        if self.auto100.get():
            self.btn.configure(text="ARM")
            self.hint.configure(
                text=f"ARM, then power on the CubeSat  (timeout {int(ARM_TIMEOUT_S)} s)")
        else:
            self.btn.configure(text="START")
            self.hint.configure(text="free-running: stop by hand")

    def _log(self, msg, tag=None):
        self.txt.insert("end", msg + "\n", tag or ())
        # Keep the widget bounded; a long run would otherwise grow without limit.
        if int(self.txt.index("end-1c").split(".")[0]) > 500:
            self.txt.delete("1.0", "200.0")
        self.txt.see("end")

    # ------------------------------------------------------------- control
    def _toggle(self):
        if self.reader:
            self._stop("manual")
        else:
            self._start()

    def _start(self):
        port = self.port_cb.get().strip()
        dist = self.dist_var.get().strip()
        run_no = self.run_var.get().strip()
        if not port:
            messagebox.showerror("No port", "Pick a serial port.")
            return
        if not dist:
            messagebox.showerror("No distance", "Enter the distance in metres.")
            return
        if self.auto100.get() and not run_no:
            messagebox.showerror("No run number", "Enter the run number.")
            return

        self.rows = []
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = dist.replace("/", "-").replace("\\", "-").replace(" ", "")
        safe_run = run_no.replace("/", "-").replace("\\", "-").replace(" ", "") or "1"

        if self.auto100.get():
            self.run100 = Run100()
            self.stats = None
            self.armed = True
            self.capturing = False
            self.run_path = LOG_DIR / f"run_{safe}m_r{safe_run}_{stamp}.csv"
        else:
            self.stats = Stats()
            self.run100 = None
            self.armed = False
            self.capturing = True
            self.run_path = LOG_DIR / f"run_{safe}m_{stamp}.csv"

        self.reader = SerialReader(port, self.q)
        self.reader.start()

        self.btn.configure(text="STOP")
        for w in (self.dist_e, self.run_e, self.auto_cb):
            w.configure(state="disabled")
        self.port_cb.configure(state="disabled")

        if self.auto100.get():
            self._log(f"=== ARMED  {dist} m  run {safe_run}  "
                      f"({self.notes_var.get()}) ===", "info")
            self._log("Waiting for CubeSat…  power it on now.", "warn")
            self.status.set("Waiting for CubeSat…")
        else:
            self._log(f"=== START  {dist} m  ({self.notes_var.get()}) ===", "info")

    def _stop(self, reason="manual"):
        if not self.reader:
            return
        if self.run100 is not None:
            self.run100.stop_reason = reason
        self.reader.stop()
        self.reader.join(timeout=3)
        self.reader = None
        self.armed = False
        self.capturing = False
        for w in (self.dist_e, self.run_e, self.auto_cb):
            w.configure(state="normal")
        self.port_cb.configure(state="readonly")
        self._mode_changed()
        self._finish()

    # -------------------------------------------------------------- saving
    def _write_rows(self):
        with open(self.run_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=OUT_FIELDS)
            w.writeheader()
            w.writerows(self.rows)

    @staticmethod
    def _append(path, fields, row):
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            if new:
                w.writeheader()
            w.writerow(row)

    def _finish(self):
        if self.run100 is not None:
            self._finish_100()
        else:
            self._finish_free()

    def _finish_100(self):
        r = self.run100
        notes = self.notes_var.get().strip()
        if not r.fresh_boot:
            notes = (notes + "; " if notes else "") + "not_fresh_boot"

        # Raw CSV is written even for an empty run, so the attempt is on record.
        self._write_rows()

        lo, hi = r.ci
        rssi_mean = round(sum(r.rssi) / len(r.rssi), 1) if r.rssi else ""
        snr_mean = round(sum(r.snr) / len(r.snr), 2) if r.snr else ""
        row = {
            "timestamp": datetime.fromtimestamp(r.armed_at).isoformat(timespec="seconds"),
            "distance_m": self.dist_var.get().strip(),
            "run": self.run_var.get().strip(),
            "notes": notes,
            "received": r.received,
            "expected": r.expected,
            "lost_pids": " ".join(str(p) for p in r.lost_pids),
            "crc_fail": r.crc_fail,
            "pdr_pct": round(r.pdr, 2),
            "ci_low_pct": round(lo, 2),
            "ci_high_pct": round(hi, 2),
            "rssi_mean": rssi_mean,
            "rssi_min": min(r.rssi) if r.rssi else "",
            "rssi_max": max(r.rssi) if r.rssi else "",
            "snr_mean": snr_mean,
            "snr_min": min(r.snr) if r.snr else "",
            "snr_max": max(r.snr) if r.snr else "",
            "cadence_s": round(r.cadence, 3),
            "duration_s": round(r.duration, 1),
            "window_start": r.window_start if r.anchored else "",
            "window_end": r.window_end if r.anchored else "",
            "first_pid_seen": r.first_pid_seen if r.anchored else "",
            "fresh_boot": "yes" if r.fresh_boot else "no",
            "stop_reason": r.stop_reason,
        }
        self._append(LOG_DIR / "summary_100pkt.csv", SUMMARY_100_FIELDS, row)

        tag = "ok" if r.received else "err"
        self._log(f"=== SAVED  {self.run_path.name}  |  "
                  f"{r.received}/{r.expected}  PDR {r.pdr:.2f}%  "
                  f"CI {lo:.1f}-{hi:.1f}%  ({r.stop_reason}) ===", tag)
        self.status.set(f"saved → {self.run_path.name}  (summary_100pkt.csv updated)")

        messagebox.showinfo(
            "Run saved",
            f"Distance : {row['distance_m']} m    Run: {row['run']}\n"
            f"Received : {r.received} / {r.expected}\n"
            f"PDR      : {r.pdr:.2f} %   (95 % CI {lo:.2f}–{hi:.2f} %)\n"
            f"CRC fail : {r.crc_fail}\n"
            f"RSSI     : {rssi_mean} dBm\n"
            f"SNR      : {snr_mean} dB\n"
            f"Cadence  : {row['cadence_s']} s\n"
            f"Duration : {row['duration_s']} s\n"
            f"Window   : {row['window_start']}..{row['window_end']}  "
            f"(fresh boot: {row['fresh_boot']})\n"
            f"Stopped  : {r.stop_reason}\n\n"
            f"Raw  → {self.run_path.name}\nSummary → summary_100pkt.csv")

        # Next run of the same point, pre-incremented for convenience.
        try:
            self.run_var.set(str(int(self.run_var.get().strip()) + 1))
        except ValueError:
            pass
        self.run100 = None

    def _finish_free(self):
        s = self.stats
        if not s or s.good == 0:
            self._log("no packets captured — nothing saved", "warn")
            self.status.set("idle — nothing captured")
            return

        self._write_rows()
        row = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "distance_m": self.dist_var.get().strip(),
            "notes": self.notes_var.get().strip(),
            "duration_s": round(s.duration, 1),
            "pdr_pct": round(s.pdr, 2),
            "usable_pct": round(s.usable, 2),
            "good": s.good, "lost": s.lost,
            "crc_fail": s.crc_fail, "parse_fail": s.parse_fail,
            "expected": s.expected, "arrived": s.arrived,
            "first_pid": s.first_pid, "last_pid": s.last_pid,
            "rssi_avg": round(sum(s.rssi) / len(s.rssi), 1) if s.rssi else "",
            "rssi_min": min(s.rssi) if s.rssi else "",
            "rssi_max": max(s.rssi) if s.rssi else "",
            "snr_avg": round(sum(s.snr) / len(s.snr), 2) if s.snr else "",
            "snr_min": min(s.snr) if s.snr else "",
            "snr_max": max(s.snr) if s.snr else "",
            "cadence_s": round(s.cadence, 2),
        }
        self._append(LOG_DIR / "summary.csv", SUMMARY_FIELDS, row)

        self._log(f"=== SAVED  {self.run_path.name}  |  PDR {s.pdr:.2f}%  "
                  f"RSSI {row['rssi_avg']} dBm ===", "info")
        self.status.set(f"saved → {self.run_path.name}  (summary.csv updated)")
        messagebox.showinfo(
            "Point saved",
            f"Distance : {row['distance_m']} m\n"
            f"PDR      : {s.pdr:.2f} %   ({s.good}/{s.expected})\n"
            f"Usable   : {s.usable:.2f} %\n"
            f"Lost     : {s.lost}    CRC fail: {s.crc_fail}    "
            f"Parse fail: {s.parse_fail}\n"
            f"RSSI     : {row['rssi_avg']} dBm  "
            f"(min {row['rssi_min']} / max {row['rssi_max']})\n"
            f"SNR      : {row['snr_avg']} dB\n"
            f"Duration : {row['duration_s']} s\n\n"
            f"Raw  → {self.run_path.name}\nSummary → summary.csv")

    # ------------------------------------------------------------ pumping
    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "line":
                    self._on_line(payload)
                elif kind == "status":
                    if not (self.armed and not self.capturing):
                        self.status.set(payload)
                elif kind == "error":
                    self._log(payload, "err")
                    self.status.set(payload)
                    messagebox.showerror("Serial error", payload)
                    if self.reader:
                        self._stop("serial_error")
        except queue.Empty:
            pass

        # The timeout has to fire from the timer, not from a frame, or a run in
        # which nothing ever arrives (100 m) would hang forever.
        if self.reader and self.run100 is not None and self.run100.timed_out():
            self._log(f"timeout after {int(ARM_TIMEOUT_S)} s — closing run", "warn")
            self._stop("timeout")

        self._refresh()
        self.root.after(120, self._pump)

    def _on_line(self, line):
        if not line:
            return

        if line.startswith("#"):
            if "PARSE_FAIL" in line:
                if self.stats:
                    self.stats.parse_fail += 1
                elif self.run100:
                    self.run100.parse_fail += 1
                self._log(line[:160], "err")
            elif "[!]" in line or "GAP" in line:
                self._log(line, "warn")
            elif "reset" in line or "mode" in line:
                self._log(line, "info")
            return

        if line.startswith("rx_ms,"):     # CSV header echo
            return

        parts = line.split(",")
        if len(parts) not in (len(CSV_FIELDS), LEGACY_FIELD_COUNT):
            return                         # partial line, or verbose leftovers

        try:
            rx_ms = int(parts[0])
            pid = int(parts[1])
            rssi = int(parts[2])
            snr = float(parts[3])
            crc_ok = parts[4] == "OK"
        except ValueError:
            return

        if self.run100 is not None:
            # Armed but idle: the run begins on the first CRC-OK frame, so a
            # stray corrupt frame from a previous run cannot start the clock.
            if not self.capturing:
                if not crc_ok:
                    self._log(f"ignored pre-start CRC FAIL p_id={pid}", "warn")
                    return
                self.capturing = True
                self.run100.armed_at = time.time()
                self.run100._anchor(pid)
                self._log(f"first frame p_id={pid} — logging started", "ok")
                if not self.run100.fresh_boot:
                    self._log(
                        f"WARNING: first p_id is {pid}, not {BOOT_PID}. The CubeSat "
                        f"was not freshly powered. Window anchored at {pid}; "
                        f"'not_fresh_boot' recorded in notes.", "err")
                self.status.set("logging")

            row = dict(zip(CSV_FIELDS, parts))
            row["pc_time"] = datetime.now().isoformat(timespec="milliseconds")
            self.rows.append(row)
            if not crc_ok:
                self._log(f"CRC FAIL  p_id={pid}  rssi={rssi}  (not counted)", "err")

            if self.run100.add(pid, rssi, snr, crc_ok, rx_ms):
                self._log(f"reached p_id {pid} ≥ window end "
                          f"{self.run100.window_end} — closing run", "ok")
                self._stop("window_complete")
            return

        # free-running mode
        s = self.stats
        if s is None:
            return
        s.add_packet(pid, rssi, snr, crc_ok)
        row = dict(zip(CSV_FIELDS, parts))
        row["pc_time"] = datetime.now().isoformat(timespec="milliseconds")
        self.rows.append(row)
        if not crc_ok:
            self._log(f"CRC FAIL  p_id={pid}  rssi={rssi}", "err")

    def _refresh(self):
        v = self.vars
        r = self.run100
        if r is not None:
            lo, hi = r.ci
            v["pdr"].set(f"{r.pdr:6.2f} %")
            v["recv"].set(f"{r.received} / {r.expected}")
            v["ci"].set(f"{lo:.1f}–{hi:.1f}")
            v["lost"].set(str(len(r.lost_pids)) if r.anchored else "—")
            v["crc"].set(str(r.crc_fail))
            v["parse"].set(str(r.parse_fail))
            v["rssi"].set(f"{sum(r.rssi)/len(r.rssi):.1f} dBm" if r.rssi else "—")
            v["snr"].set(f"{sum(r.snr)/len(r.snr):.1f} dB" if r.snr else "—")
            if self.capturing:
                remain = max(0, ARM_TIMEOUT_S - r.armed_elapsed)
                v["elapsed"].set(f"{int(r.armed_elapsed)} s  (−{int(remain)})")
            else:
                v["elapsed"].set("armed")
            v["cadence"].set(f"{r.cadence:.3f} s" if r.cadence else "—")
            v["range"].set(f"{r.window_start}..{r.window_end}" if r.anchored else "—")
            v["gaps"].set("logging" if self.capturing else "waiting")
            return

        s = self.stats
        if not s:
            return
        v["pdr"].set(f"{s.pdr:6.2f} %")
        v["recv"].set(f"{s.good} / {s.expected}")
        v["ci"].set("—")
        v["lost"].set(str(s.lost))
        v["crc"].set(str(s.crc_fail))
        v["parse"].set(str(s.parse_fail))
        v["rssi"].set(f"{sum(s.rssi)/len(s.rssi):.1f} dBm" if s.rssi else "—")
        v["snr"].set(f"{sum(s.snr)/len(s.snr):.1f} dB" if s.snr else "—")
        v["elapsed"].set(f"{int(s.duration)} s" if self.reader else
                         f"{int(s.duration)} s (stopped)")
        v["cadence"].set(f"{s.cadence:.2f} s" if s.cadence else "—")
        v["range"].set(f"{s.first_pid}..{s.last_pid}"
                       if s.first_pid is not None else "—")
        v["gaps"].set(f"{len(s.gaps)} gaps")


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = App(root)

    def on_close():
        if app.reader:
            app.reader.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
