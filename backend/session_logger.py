#session_logger.py - archive each live dashboard session as CSV
#
# The raw serial line is written through verbatim rather than re-serialized from
# the parsed packet. That keeps the file byte-identical in format to what
# Receiver/tools/range_logger.py produces and to the receiver's own output, so
# no float ever changes value on the way to disk.

import time
from pathlib import Path

from utils.parser import CSV_HEADER


class SessionLogger:
    """Writes CSV rows to range_test_logs/, opening the file on the first row."""

    def __init__(self, log_dir: Path, enabled: bool = True):
        self.log_dir = Path(log_dir)
        self.enabled = enabled
        self.path = None
        self._fh = None
        self.rows = 0

    def write(self, raw_line: str):
        if not self.enabled:
            return
        if self._fh is None and not self._open():
            return
        self._fh.write(raw_line.strip() + "\n")
        # Flushed per row: a range test that ends with a dead battery or a
        # yanked USB cable must not lose its last minute of data to a buffer.
        self._fh.flush()
        self.rows += 1

    def _open(self) -> bool:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            self.path = self.log_dir / f"session_{stamp}.csv"
            self._fh = open(self.path, "w", encoding="utf-8", newline="")
            self._fh.write(CSV_HEADER + "\n")
            print(f"[LOG] session -> {self.path}")
            return True
        except OSError as exc:
            # Logging is not worth taking the telemetry stream down for.
            print(f"[LOG] disabled — could not open log file: {exc}")
            self.enabled = False
            return False

    def close(self):
        if self._fh:
            self._fh.close()
            self._fh = None
            print(f"[LOG] session closed: {self.path} ({self.rows} rows)")
