#!/usr/bin/env python3
"""
TT&C LoRa range-test logger.

Type the distance, hit Start, walk. Each run writes its own raw CSV and appends
one row to summary.csv so the whole distance sweep ends up in a single table.

PDR is computed here from the p_id sequence rather than trusting the receiver's
own counters, so a mid-run board reset can't silently corrupt a measurement.

Run:  py -3.14 tools/range_logger.py
"""

import csv
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

SUMMARY_FIELDS = [
    "timestamp", "distance_m", "notes", "duration_s",
    "pdr_pct", "usable_pct", "good", "lost", "crc_fail", "parse_fail",
    "expected", "arrived", "first_pid", "last_pid",
    "rssi_avg", "rssi_min", "rssi_max", "snr_avg", "snr_min", "snr_max",
    "cadence_s",
]


class Stats:
    """Link statistics for one measurement point, derived from p_id continuity."""

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
            # what was sent, so this point has to be redone — say so loudly
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
            # wired to EN/IO0, and asserting them reboots the board — which
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
        self.out_q.put(("status", "logging"))

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
        root.minsize(760, 560)

        self.reader = None
        self.q = queue.Queue()
        self.stats = None
        self.rows = []
        self.run_path = None

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

        ttk.Label(top, text="Notes").grid(row=1, column=0, sticky="w", **pad)
        self.notes_var = tk.StringVar(value="LOS")
        ttk.Entry(top, textvariable=self.notes_var, width=48).grid(
            row=1, column=1, columnspan=4, sticky="we", **pad)

        self.btn = ttk.Button(top, text="START", width=14, command=self._toggle)
        self.btn.grid(row=0, column=5, rowspan=2, sticky="nsew", **pad)
        top.columnconfigure(4, weight=1)

        # live stats
        live = ttk.LabelFrame(self.root, text="Live")
        live.pack(fill="x", **pad)
        self.vars = {}
        cells = [
            ("PDR", "pdr", 0, 0), ("Usable", "usable", 0, 1),
            ("Good", "good", 0, 2), ("Lost", "lost", 0, 3),
            ("CRC fail", "crc", 1, 0), ("Parse fail", "parse", 1, 1),
            ("RSSI avg", "rssi", 1, 2), ("SNR avg", "snr", 1, 3),
            ("Elapsed", "elapsed", 2, 0), ("Cadence", "cadence", 2, 1),
            ("p_id range", "range", 2, 2), ("Gaps", "gaps", 2, 3),
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

        self.status = tk.StringVar(value="idle")
        ttk.Label(self.root, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    @staticmethod
    def _ports():
        return [p.device for p in serial.tools.list_ports.comports()]

    def _log(self, msg, tag=None):
        self.txt.insert("end", msg + "\n", tag or ())
        # Keep the widget bounded; a long run would otherwise grow without limit.
        if int(self.txt.index("end-1c").split(".")[0]) > 500:
            self.txt.delete("1.0", "200.0")
        self.txt.see("end")

    # ------------------------------------------------------------- control
    def _toggle(self):
        if self.reader:
            self._stop()
        else:
            self._start()

    def _start(self):
        port = self.port_cb.get().strip()
        dist = self.dist_var.get().strip()
        if not port:
            messagebox.showerror("No port", "Pick a serial port.")
            return
        if not dist:
            messagebox.showerror("No distance", "Enter the distance in metres.")
            return

        self.stats = Stats()
        self.rows = []
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = dist.replace("/", "-").replace("\\", "-").replace(" ", "")
        self.run_path = LOG_DIR / f"run_{safe}m_{stamp}.csv"

        self.reader = SerialReader(port, self.q)
        self.reader.start()

        self.btn.configure(text="STOP")
        self.dist_e.configure(state="disabled")
        self.port_cb.configure(state="disabled")
        self._log(f"=== START  {dist} m  ({self.notes_var.get()}) ===", "info")

    def _stop(self):
        if not self.reader:
            return
        self.reader.stop()
        self.reader.join(timeout=3)
        self.reader = None
        self.btn.configure(text="START")
        self.dist_e.configure(state="normal")
        self.port_cb.configure(state="readonly")
        self._finish()

    def _finish(self):
        s = self.stats
        if not s or s.good == 0:
            self._log("no packets captured — nothing saved", "warn")
            self.status.set("idle — nothing captured")
            return

        with open(self.run_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            w.writeheader()
            w.writerows(self.rows)

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
        summary = LOG_DIR / "summary.csv"
        new = not summary.exists()
        with open(summary, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=SUMMARY_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)

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
                    self.status.set(payload)
                elif kind == "error":
                    self._log(payload, "err")
                    self.status.set(payload)
                    messagebox.showerror("Serial error", payload)
                    if self.reader:
                        self._stop()
        except queue.Empty:
            pass
        self._refresh()
        self.root.after(120, self._pump)

    def _on_line(self, line):
        if not line:
            return
        s = self.stats
        if s is None:
            return

        if line.startswith("#"):
            if "PARSE_FAIL" in line:
                s.parse_fail += 1
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
            pid = int(parts[1])
            rssi = int(parts[2])
            snr = float(parts[3])
            crc_ok = parts[4] == "OK"
        except ValueError:
            return

        s.add_packet(pid, rssi, snr, crc_ok)
        self.rows.append(dict(zip(CSV_FIELDS, parts)))
        if not crc_ok:
            self._log(f"CRC FAIL  p_id={pid}  rssi={rssi}", "err")

    def _refresh(self):
        s = self.stats
        if not s:
            return
        v = self.vars
        v["pdr"].set(f"{s.pdr:6.2f} %")
        v["usable"].set(f"{s.usable:6.2f} %")
        v["good"].set(str(s.good))
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
        v["gaps"].set(str(len(s.gaps)))


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
