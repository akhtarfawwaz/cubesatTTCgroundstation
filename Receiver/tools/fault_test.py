#!/usr/bin/env python3
"""
Command-uplink fault-injection test for the TT&C subsystem.

Exercises every rejection path in the CubeSat's handle_command() over the air
and checks the acknowledgement that comes back in the `ack` field of the next
telemetry frames.

  T1  invalid command type     >N01,9,0                                NAK
  T2  invalid mode parameter   >N02,1,7                                NAK
  T3  valid command            >N03,3,200                              ACK N03 + one beep
  T4  duplicate command        >N03,3,200 again                        ACK N03, NO second beep
  T5  malformed JSON           !{"id":N04,"ct":3,"pm":200              NAK
  T6  bad CRC                  !{"id":N05,"ct":3,"pm":200,"c":"0000"}  NAK
  T7  not JSON                 !hello                                  NAK
  T8  valid after faults       >N06,1,2                                ACK N06, mode -> 2
  T9  no satellite             >N07,3,200 with the CubeSat unplugged   TIMEOUT

T5-T7 need the receiver flashed with [env:receiver_faulttest], which adds the
'!<text>' raw-uplink command. The script probes for it and marks those tests
SKIP rather than FAIL if the normal build is loaded.

Two details that make the verdicts trustworthy:

  Stale-ack guard. The satellite's `ack` is sticky for the rest of its session,
  so a test that expects NAK could "pass" on a -1 left over from the previous
  test. Before every such test a priming command is sent so `ack` is known to
  differ from the expected value, and only frames received after the uplink are
  considered.

  T4 is primed with an INVALID command, not a valid one. handle_command() sets
  last_cmd_id only on success, so an invalid frame moves `ack` to -1 without
  disturbing the duplicate-detection state. Priming T4 with a valid command
  would overwrite last_cmd_id and the resent N03 would be executed again --
  the dedupe window is exactly one command deep.

Run:  backend/venv/Scripts/python.exe Receiver/tools/fault_test.py [COM3]
"""

import argparse
import csv
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import serial

sys.path.insert(0, str(Path(__file__).resolve().parent))
from range_logger import CSV_FIELDS, LEGACY_FIELD_COUNT, LOG_DIR

BAUD = 115200
ACK_NAK = -1
ACK_NONE = 0
FRAMES_AFTER = 4          # frames inspected after each uplink
FRAME_TIMEOUT = 6.0       # per-frame wait; cadence is 2 s
T9_WINDOW = 8.0           # matches backend COMMAND_TIMEOUT
DUP_DELAY = 6.0           # T4 is sent this long after T3

IDX_PID = CSV_FIELDS.index("p_id")
IDX_CRC = CSV_FIELDS.index("crc")
IDX_MODE = CSV_FIELDS.index("mode")
IDX_ACK = CSV_FIELDS.index("ack")


class Frame:
    __slots__ = ("pc_time", "p_id", "crc_ok", "mode", "ack", "raw")

    def __init__(self, parts, raw):
        self.pc_time = datetime.now().isoformat(timespec="milliseconds")
        self.p_id = int(parts[IDX_PID])
        self.crc_ok = parts[IDX_CRC] == "OK"
        self.mode = int(parts[IDX_MODE])
        self.ack = int(parts[IDX_ACK])
        self.raw = raw


class Session:
    """Owns the port, mirrors everything to a log file, hands up frames."""

    def __init__(self, port, log_path):
        self.frames = queue.Queue()
        self.comments = queue.Queue()
        self._stop = threading.Event()

        # Port first, log second: a port that cannot be opened must not leave an
        # empty log file lying in range_test_logs.
        # DTR/RTS are wired to EN/IO0 on a CH340 ESP32; asserting them reboots
        # the receiver and would wipe its state the moment we connect.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = BAUD
        self.ser.timeout = 1
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        time.sleep(0.4)
        self.ser.reset_input_buffer()

        self.log = open(log_path, "w", encoding="utf-8", newline="")
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _write_log(self, direction, text):
        self.log.write("%s %s %s\n"
                       % (datetime.now().isoformat(timespec="milliseconds"),
                          direction, text))
        self.log.flush()

    def _read(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                self._write_log("ERR", "serial read failed: %s" % exc)
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._write_log("RX ", line)
                if line.startswith("#"):
                    self.comments.put(line)
                    continue
                if line.startswith("rx_ms,"):
                    continue
                parts = line.split(",")
                if len(parts) not in (len(CSV_FIELDS), LEGACY_FIELD_COUNT):
                    continue
                try:
                    self.frames.put(Frame(parts, line))
                except (ValueError, IndexError):
                    continue

    def send(self, text):
        self._write_log("TX ", text)
        self.ser.write((text + "\n").encode())
        self.ser.flush()

    def send_key(self, key):
        self._write_log("TX ", "<key %r>" % key)
        self.ser.write(key.encode())
        self.ser.flush()

    def drain(self):
        for q in (self.frames, self.comments):
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break

    def next_frame(self, timeout=FRAME_TIMEOUT):
        try:
            return self.frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def collect_comments(self):
        out = []
        while True:
            try:
                out.append(self.comments.get_nowait())
            except queue.Empty:
                return out

    def close(self):
        self._stop.set()
        self.t.join(timeout=2)
        try:
            if self.ser.is_open:
                self.ser.write(b"b")
                time.sleep(0.1)
                self.ser.close()
        except Exception:
            pass
        self.log.close()


def ask(prompt, valid=("y", "n", "s")):
    while True:
        a = input(prompt).strip().lower()
        if a in valid:
            return a
        print("   please answer one of: %s" % ", ".join(valid))


class Runner:
    def __init__(self, sess, results):
        self.s = sess
        self.results = results
        self.last_pid = None

    # -- helpers ---------------------------------------------------------
    def _note_pid(self, frames):
        if frames:
            self.last_pid = frames[-1].p_id

    def prime(self, cmd_id, valid=True, label=""):
        """Move `ack` to a known value so a stale one cannot fake a PASS.

        valid=True  -> ACK cmd_id (also overwrites last_cmd_id)
        valid=False -> NAK (-1) without touching last_cmd_id
        """
        line = ">%d,3,150" % cmd_id if valid else ">%d,9,0" % cmd_id
        want = cmd_id if valid else ACK_NAK
        self.s.drain()
        self.s.send(line)
        deadline = time.time() + 14
        while time.time() < deadline:
            f = self.s.next_frame()
            if f is None:
                break
            self.last_pid = f.p_id
            if f.ack == want:
                print("   primed: ack=%d via %s %s" % (f.ack, line, label))
                return True
        print("   WARNING: priming %s did not take effect (ack never became %d)"
              % (line, want))
        return False

    def run(self, tid, line, expect, desc, raw=False, beep_q=None,
            expect_mode=None, window=None):
        """Send one test and judge the next FRAMES_AFTER frames."""
        pid_before = self.last_pid
        self.s.drain()
        sent_at = datetime.now().isoformat(timespec="milliseconds")
        t0 = time.time()
        self.s.send(line)

        frames = []
        if window is None:
            for _ in range(FRAMES_AFTER):
                f = self.s.next_frame()
                if f is None:
                    break
                frames.append(f)
        else:
            # T9: listen for a fixed wall-clock window instead of N frames.
            while time.time() - t0 < window:
                f = self.s.next_frame(timeout=max(0.2, window - (time.time() - t0)))
                if f is not None:
                    frames.append(f)

        acks = [f.ack for f in frames]
        first_pid = frames[0].p_id if frames else None
        lost_before = ("" if (pid_before is None or first_pid is None)
                       else max(0, first_pid - pid_before - 1))
        lost_within = sum(max(0, frames[i + 1].p_id - frames[i].p_id - 1)
                          for i in range(len(frames) - 1))
        self._note_pid(frames)
        comments = [c for c in self.s.collect_comments()
                    if "UPLINK_" in c or "RAW_" in c or "GAP" in c]

        # ---- verdict ----
        if expect == "TIMEOUT":
            ok = len(frames) == 0
            detail = ("no frames in %.0f s" % window if ok
                      else "%d frame(s) arrived" % len(frames))
        else:
            ok = expect in acks
            detail = "ack %s in %s" % (expect, acks) if ok else \
                     "expected ack %s, saw %s" % (expect, acks)
            if ok and expect_mode is not None:
                modes = [f.mode for f in frames if f.ack == expect]
                if not any(m == expect_mode for m in modes):
                    ok = False
                    detail += "; mode never became %d (saw %s)" % (expect_mode, modes)

        beep = ""
        if beep_q is not None:
            a = ask("   %s [y/n, s=skip] " % beep_q)
            beep = {"y": "yes", "n": "no", "s": "skipped"}[a]
            if a == "n":
                ok = False
                detail += "; operator reported wrong beep behaviour"

        verdict = "PASS" if ok else "FAIL"
        print("   %-4s %-5s %s" % (tid, verdict, detail))

        self.results.append({
            "test": tid, "description": desc, "line_sent": line,
            "raw_uplink": "yes" if raw else "no",
            "pc_time_sent": sent_at,
            "expected": expect,
            "first_pid_after": "" if first_pid is None else first_pid,
            "acks_next_%d" % FRAMES_AFTER: " ".join(str(a) for a in acks),
            "frames_seen": len(frames),
            "lost_before_uplink": lost_before,
            "lost_within_window": lost_within,
            "mode_seen": " ".join(str(f.mode) for f in frames),
            "beep_answer": beep,
            "verdict": verdict,
            "detail": detail,
            "receiver_comments": " | ".join(comments)[:300],
        })
        return ok


def repetition(sess, results, rep, base, have_fault):
    print("\n=== repetition %d  (cmd ids %d..%d) ===" % (rep + 1, base + 1, base + 7))
    r = Runner(sess, results)
    pbase = base + 50

    # Establish a baseline p_id and make sure frames are flowing.
    f = sess.next_frame(timeout=12)
    if f is None:
        print("   no telemetry — is the CubeSat powered and in range?")
    else:
        r.last_pid = f.p_id
        print("   link alive, p_id=%d ack=%d mode=%d" % (f.p_id, f.ack, f.mode))

    # T1 -- invalid ct. Prime valid so a stale -1 cannot fake the NAK.
    r.prime(pbase + 1, valid=True, label="(for T1)")
    r.run("T1", ">%d,9,0" % (base + 1), ACK_NAK, "invalid command type ct=9")

    # T2 -- invalid mode parameter.
    r.prime(pbase + 2, valid=True, label="(for T2)")
    r.run("T2", ">%d,1,7" % (base + 2), ACK_NAK, "invalid mode parameter pm=7")

    # T3 -- valid baseline. ack cannot already be base+3: ids are unique.
    t3_at = time.time()
    r.run("T3", ">%d,3,200" % (base + 3), base + 3,
          "valid beep command", beep_q="Did you hear exactly ONE beep?")

    # T4 -- duplicate. Primed with an INVALID command so ack moves off base+3
    # without resetting last_cmd_id (see module docstring).
    r.prime(pbase + 3, valid=False, label="(for T4, NAK prime)")
    wait = DUP_DELAY - (time.time() - t3_at)
    if wait > 0:
        print("   waiting %.1f s before the duplicate" % wait)
        time.sleep(wait)
    r.run("T4", ">%d,3,200" % (base + 3), base + 3,
          "duplicate of T3 -- re-ack, must NOT re-execute",
          beep_q="Did you hear NO second beep?")

    # T5..T7 -- malformed frames; need the FAULT_TEST build.
    faults = [
        ("T5", '!{"id":%d,"ct":3,"pm":200' % (base + 4), "malformed JSON"),
        ("T6", '!{"id":%d,"ct":3,"pm":200,"c":"0000"}' % (base + 5), "bad CRC"),
        ("T7", "!hello", "not JSON"),
    ]
    for tid, line, desc in faults:
        if not have_fault:
            print("   %-4s SKIP  needs [env:receiver_faulttest]" % tid)
            results.append({
                "test": tid, "description": desc, "line_sent": line,
                "raw_uplink": "yes", "pc_time_sent": "", "expected": ACK_NAK,
                "first_pid_after": "", "acks_next_%d" % FRAMES_AFTER: "",
                "frames_seen": 0, "lost_before_uplink": "",
                "lost_within_window": "", "mode_seen": "", "beep_answer": "",
                "verdict": "SKIP",
                "detail": "receiver not flashed with FAULT_TEST",
                "receiver_comments": "",
            })
            continue
        r.prime(pbase + 4 + faults.index((tid, line, desc)), valid=True,
                label="(for %s)" % tid)
        r.run(tid, line, ACK_NAK, desc, raw=True)

    # T8 -- a valid command still works after all that.
    r.run("T8", ">%d,1,2" % (base + 6), base + 6,
          "valid command after faults, mode -> 2", expect_mode=2)

    # T9 -- nothing on the other end.
    print("\n   T9 needs the CubeSat OFF.")
    if ask("   Unplug/power off the CubeSat now. Ready? [y/n, s=skip] ") == "y":
        time.sleep(1.0)
        sess.drain()
        r.run("T9", ">%d,3,200" % (base + 7), "TIMEOUT",
              "no satellite -- expect no telemetry and no ack",
              window=T9_WINDOW)
        ask("   Power the CubeSat back ON. Ready? [y/n, s=skip] ")
        print("   note: the reboot resets p_id to 1 and clears ack/last_cmd_id,"
              " so the next repetition starts from a clean state.")
    else:
        print("   T9 SKIP")
        results.append({
            "test": "T9", "description": "no satellite", "line_sent": "",
            "raw_uplink": "no", "pc_time_sent": "", "expected": "TIMEOUT",
            "first_pid_after": "", "acks_next_%d" % FRAMES_AFTER: "",
            "frames_seen": 0, "lost_before_uplink": "", "lost_within_window": "",
            "mode_seen": "", "beep_answer": "", "verdict": "SKIP",
            "detail": "operator skipped", "receiver_comments": "",
        })


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("port", nargs="?", default="COM3")
    ap.add_argument("--reps", type=int, default=3)
    args = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = LOG_DIR / ("fault_test_%s.log" % stamp)
    csv_path = LOG_DIR / ("fault_test_%s.csv" % stamp)

    print("fault_test  port=%s  reps=%d" % (args.port, args.reps))
    print("raw serial -> %s" % log_path.name)
    try:
        sess = Session(args.port, log_path)
    except Exception as exc:
        print("could not open %s: %s" % (args.port, exc))
        print("Close the PlatformIO monitor, the backend and range_logger first "
              "-- only one process may hold the port.")
        return 2

    results = []
    try:
        sess.send_key("c")                 # CSV output
        time.sleep(0.3)
        sess.send_key("h")                 # help banner reveals the build
        time.sleep(1.0)
        banner = sess.collect_comments()
        have_fault = any("FAULT_TEST" in c for c in banner)
        print("receiver build: %s"
              % ("FAULT_TEST (raw uplink available)" if have_fault
                 else "normal -- T5/T6/T7 will be SKIPPED"))
        if not have_fault:
            print("  flash it with:  pio run -e receiver_faulttest -t upload")

        for rep in range(args.reps):
            repetition(sess, results, rep, 900 + rep * 100, have_fault)
    except KeyboardInterrupt:
        print("\ninterrupted — saving what was collected")
    finally:
        sess.close()

    if not results:
        print("no results")
        return 1

    fields = list(results[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(results)

    # ---- Markdown summary ----
    print("\n\n## Fault-injection results\n")
    print("| Test | Description | Sent | Expected | Acks seen | Lost | Beep | Verdict |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print("| %s | %s | `%s` | %s | %s | %s | %s | **%s** |" % (
            r["test"], r["description"], r["line_sent"], r["expected"],
            r["acks_next_%d" % FRAMES_AFTER] or "—",
            r["lost_before_uplink"], r["beep_answer"] or "—", r["verdict"]))

    tally = {}
    for r in results:
        tally[r["verdict"]] = tally.get(r["verdict"], 0) + 1
    print("\n" + "  ".join("%s: %d" % kv for kv in sorted(tally.items())))
    print("\nraw log  -> %s" % log_path)
    print("results  -> %s" % csv_path)
    return 0 if tally.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
