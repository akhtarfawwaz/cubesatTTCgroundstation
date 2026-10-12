#link_stats.py - RF link quality for the live session
#
# Ported from Stats in Receiver/tools/range_logger.py rather than imported: that
# file is a tkinter application and importing it would pull Tk into the Flask
# process. The arithmetic is deliberately identical so a dashboard session and a
# range_logger session report the same PDR for the same link.

import time

# Same thresholds as rssi_label() in Receiver/src/main.cpp, so the dashboard and
# the receiver's own verbose output never disagree about link quality.
_RSSI_LABELS = [
    (-60,  "EXCELLENT"),
    (-80,  "GOOD"),
    (-100, "FAIR"),
    (-110, "WEAK"),
]


def rssi_label(rssi: int | None) -> str:
    if rssi is None:
        return "NO DATA"
    for threshold, label in _RSSI_LABELS:
        if rssi > threshold:
            return label
    return "MARGINAL"


class LinkStats:
    """Link statistics for the current session, derived from p_id continuity."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.first_pid = None
        self.last_pid = None
        self.good = 0
        self.crc_fail = 0
        self.parse_fail = 0
        self.rssi = []
        self.snr = []
        self.gaps = []          # (from_pid, to_pid)
        self.started = time.time()
        self.last_rx = None
        self.reboots = 0

    def add_packet(self, pid: int, rssi: int, snr: float, crc_ok: bool):
        if self.first_pid is None:
            self.first_pid = pid
            # Start the clock at the first packet, not at construction: the
            # backend is up for some seconds before the port opens and the
            # link starts, and counting that idle time inflates the reported
            # cadence for the whole early part of a session.
            self.started = time.time()
        elif pid > self.last_pid + 1:
            self.gaps.append((self.last_pid + 1, pid - 1))
        elif pid < self.last_pid:
            # Transmitter rebooted mid-session. The p_id range no longer
            # describes what was sent, so PDR computed from it would be
            # meaningless — start the measurement over.
            self.reboots += 1
            self.first_pid = pid
            self.gaps.clear()
            self.good = self.crc_fail = 0
            self.rssi.clear()
            self.snr.clear()
            self.started = time.time()

        self.last_pid = pid
        self.last_rx = time.time()

        if crc_ok:
            self.good += 1
            self.rssi.append(rssi)
            self.snr.append(snr)
        else:
            self.crc_fail += 1

    def add_parse_fail(self):
        # Only ever reported as a '# PARSE_FAIL' comment — a frame that would
        # not deserialize has no readable p_id, so it cannot be counted above.
        self.parse_fail += 1

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

    def snapshot(self) -> dict:
        rssi_avg = sum(self.rssi) / len(self.rssi) if self.rssi else None
        snr_avg = sum(self.snr) / len(self.snr) if self.snr else None
        return {
            "good": self.good,
            "lost": self.lost,
            "crc_fail": self.crc_fail,
            "parse_fail": self.parse_fail,
            "expected": self.expected,
            "arrived": self.arrived,
            "first_pid": self.first_pid,
            "last_pid": self.last_pid,
            "pdr": round(self.pdr, 2),
            "usable": round(self.usable, 2),
            "cadence": round(self.cadence, 2),
            "duration": round(self.duration, 1),
            "reboots": self.reboots,
            "gaps": len(self.gaps),
            "rssi_avg": round(rssi_avg, 1) if rssi_avg is not None else None,
            "rssi_min": min(self.rssi) if self.rssi else None,
            "rssi_max": max(self.rssi) if self.rssi else None,
            "rssi_label": rssi_label(round(rssi_avg) if rssi_avg is not None else None),
            "snr_avg": round(snr_avg, 1) if snr_avg is not None else None,
            "snr_min": min(self.snr) if self.snr else None,
            "snr_max": max(self.snr) if self.snr else None,
            "last_rx": self.last_rx,
        }
