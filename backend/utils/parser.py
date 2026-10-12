#Telemetry Parser — receiver serial CSV -> dashboard packet
#
# The ESP32 receiver does NOT forward the raw LoRa JSON over USB. It decodes the
# frame, checks the CRC itself, and prints a 26-column CSV row (see set_rx_mode()
# in Receiver/src/main.cpp). So the ground station's job here is not to validate
# a CRC — that already happened on the board, and its verdict arrives as the
# literal string OK/FAIL in column 5 — but to turn that row into the flat packet
# the dashboard already expects, plus the RF fields it never had access to.

import time

# Byte-identical to the header the firmware prints (set_rx_mode() in
# Receiver/src/main.cpp). alt_kf — the Kalman-fused altitude — is appended last
# rather than slotted next to alt so the archived 26-column range-test logs
# still parse; anything read by column name is unaffected.
CSV_FIELDS = [
    "rx_ms", "p_id", "rssi", "snr", "crc", "ts", "mode", "temp", "press", "alt",
    "ax", "ay", "az", "gx", "gy", "gz", "roll", "pitch", "yaw",
    "lat", "lon", "gps_alt", "sats", "volt", "err", "ack",
    "alt_kf",
]

CSV_HEADER = ",".join(CSV_FIELDS)

# Rows written before the Kalman filter existed stop here.
LEGACY_FIELD_COUNT = len(CSV_FIELDS) - 1


def _crc_ok(value: str) -> bool:
    return value == "OK"


# (csv column, dashboard key, converter). The renames exist because the UI was
# built against the long-form mock names; the units are the firmware's and are
# NOT converted — accel is in g and gyro in deg/s, not m/s^2 and rad/s.
_SCHEMA = [
    ("rx_ms",   "rx_ms",       int),
    ("p_id",    "p_id",        int),
    ("rssi",    "rssi",        int),
    ("snr",     "snr",         float),
    ("crc",     "crc_ok",      _crc_ok),
    ("ts",      "timestamp",   int),
    ("mode",    "mode",        int),
    ("temp",    "temperature", float),
    ("press",   "pressure",    int),
    ("alt",     "altitude",    float),
    ("ax",      "accel_x",     float),
    ("ay",      "accel_y",     float),
    ("az",      "accel_z",     float),
    ("gx",      "gyro_x",      float),
    ("gy",      "gyro_y",      float),
    ("gz",      "gyro_z",      float),
    ("roll",    "roll",        float),
    ("pitch",   "pitch",       float),
    ("yaw",     "yaw",         float),
    ("lat",     "latitude",    float),
    ("lon",     "longitude",   float),
    ("gps_alt", "gps_alt",     float),
    ("sats",    "sats",        int),
    ("volt",    "volt",        float),
    ("err",     "err",         int),
    ("ack",     "ack",         int),      # cmd_id of the executed command, or -1 (NAK)
    ("alt_kf",  "alt_kf",      float),
]

# ack semantics, thesis Table 4.7 / §4.6.3
ACK_NONE = 0
ACK_NAK = -1


def parse_csv_row(line: str) -> dict | None:
    """
    Parse one CSV telemetry row from the receiver.

    Returns None for anything that is not a data row: '#' comments, the header
    echo, and partial lines (the first read after opening the port routinely
    lands mid-row).
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("rx_ms,"):
        return None

    parts = line.split(",")
    if len(parts) not in (len(CSV_FIELDS), LEGACY_FIELD_COUNT):
        return None

    packet = {}
    try:
        for (_, key, cast), raw in zip(_SCHEMA, parts):
            packet[key] = cast(raw)
    except ValueError:
        # A truncated row that still happens to have the right comma count.
        return None

    # Replaying a pre-Kalman archive: fall back to the raw barometric altitude
    # so the dashboard has something to plot rather than a hole.
    packet.setdefault("alt_kf", packet["altitude"])

    # The firmware's "ts" is GPS seconds-of-day, or millis()/1000 with no fix —
    # never a wall clock. The dashboard needs a real one, so stamp arrival here.
    packet["rx_time"] = time.time()
    return packet


# Comment kinds worth surfacing in the UI event log. The rolling LINK SUMMARY
# block is deliberately absent: the backend computes the same numbers itself in
# link_stats.py, and echoing twenty box-drawing lines every 20 packets would
# bury the events that matter.
def parse_comment(line: str) -> dict | None:
    """Classify a '#' line from the receiver into an event, or None to ignore."""
    line = line.strip()
    if not line.startswith("#"):
        return None

    text = line.lstrip("# ").strip()
    if not text:
        return None

    if "PARSE_FAIL" in line:
        kind = "parse_fail"
    elif "GAP" in line:
        kind = "gap"
    elif "UPLINK_" in line:
        kind = "uplink"
    elif "went backwards" in line:
        kind = "reset"
    elif line.startswith("# listening"):
        kind = "heartbeat"
    elif line.startswith("# |") or line.startswith("# +"):
        return None
    else:
        return None

    return {"kind": kind, "text": text, "rx_time": time.time()}
