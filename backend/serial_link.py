#serial_link.py - owns the receiver's USB serial port
#
# Modeled on SerialReader in Receiver/tools/range_logger.py, with reconnection
# added: a dashboard is expected to stay up across a USB unplug, where a range
# test run is not.
#
# Only one process can hold the port. If the PlatformIO monitor, a VS Code
# serial terminal, or range_logger.py is attached, opening here fails and the
# link retries until they let go.

import threading
import time

import serial


class SerialLink(threading.Thread):
    """Reads decoded lines off the receiver and writes uplink commands to it."""

    def __init__(self, port, baud, on_line, on_status, retry_interval=2.0):
        super().__init__(daemon=True)
        self.port = port
        self.baud = baud
        self.on_line = on_line
        self.on_status = on_status
        self.retry_interval = retry_interval

        self.ser = None
        self._stop = threading.Event()
        self._write_lock = threading.Lock()
        self.state = "closed"

    # ------------------------------------------------------------- lifecycle
    def run(self):
        while not self._stop.is_set():
            if not self._open():
                self._stop.wait(self.retry_interval)
                continue
            self._read_loop()
            self._close()
            if not self._stop.is_set():
                self._stop.wait(self.retry_interval)
        self._set_state("closed")

    def stop(self):
        self._stop.set()

    def _set_state(self, state, detail=""):
        self.state = state
        self.on_status({"state": state, "port": self.port, "detail": detail})

    # ------------------------------------------------------------- port I/O
    def _open(self) -> bool:
        self._set_state("opening")
        try:
            # Leave DTR/RTS deasserted: on a CH340-based ESP32 those lines are
            # wired to EN/IO0, so asserting them reboots the board the instant
            # we connect — which would wipe the receiver's stats every time the
            # backend restarts.
            self.ser = serial.Serial()
            self.ser.port = self.port
            self.ser.baudrate = self.baud
            self.ser.timeout = 1
            self.ser.dtr = False
            self.ser.rts = False
            self.ser.open()
        except Exception as exc:
            self.ser = None
            self._set_state("error", str(exc))
            return False

        # The receiver boots into verbose mode, so it has to be told to speak
        # CSV. Both commands are idempotent.
        time.sleep(0.4)
        try:
            self.ser.reset_input_buffer()
            self.ser.write(b"c")      # force CSV mode
            time.sleep(0.2)
            self.ser.write(b"r")      # reset the receiver's own counters
        except Exception as exc:
            self._set_state("error", f"handshake failed: {exc}")
            return False

        self._set_state("open")
        return True

    def _read_loop(self):
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                self._set_state("error", f"read failed: {exc}")
                return
            if not chunk:
                continue
            buf += chunk
            # Split on newlines rather than using readline(), so a partial row
            # sitting in the buffer never blocks the rows behind it.
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self.on_line(raw.decode("utf-8", "replace").strip())

    def _close(self):
        try:
            if self.ser and self.ser.is_open:
                with self._write_lock:
                    self.ser.write(b"b")   # hand the board back in readable mode
                time.sleep(0.1)
                self.ser.close()
        except Exception:
            pass
        self.ser = None

    # ------------------------------------------------------------- commands
    def send_command(self, cmd_id: int, cmd_type: int, param: int) -> bool:
        """
        Queue an uplink on the receiver. It transmits on the next downlink,
        which is the only moment the satellite's 500 ms receive window is open.

        cmd_id is what comes back in the telemetry 'ack' field (thesis Table
        4.8), so it — not cmd_type — is what identifies the acknowledgement.
        """
        if self.state != "open" or self.ser is None:
            return False
        line = f">{int(cmd_id)},{int(cmd_type)},{int(param)}\n".encode("ascii")
        try:
            with self._write_lock:
                self.ser.write(line)
            return True
        except Exception as exc:
            self._set_state("error", f"write failed: {exc}")
            return False
