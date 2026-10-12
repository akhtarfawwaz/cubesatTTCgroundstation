#config.py - Ground Station Configuration
#
# Every value can be overridden from the environment so the same checkout runs
# on a machine where the receiver enumerates as something other than COM3.

import os
from pathlib import Path

#Serial port settings — must match platformio.ini [env:receiver] and the
#firmware's Serial.begin() rate.
SERIAL_PORT = os.environ.get("TTC_SERIAL_PORT", "COM3")
SERIAL_BAUD = int(os.environ.get("TTC_SERIAL_BAUD", "115200"))

#Seconds between reconnect attempts when the port is missing or in use.
SERIAL_RETRY_INTERVAL = 2.0

#Flask server settings
FLASK_HOST = os.environ.get("TTC_HOST", "0.0.0.0")
FLASK_PORT = int(os.environ.get("TTC_PORT", "5000"))
FLASK_DEBUG = os.environ.get("TTC_DEBUG", "1") == "1"

#LoRa settings — informational, mirrored from the firmware for the UI banner
LORA_FREQUENCY = 433
LORA_BANDWIDTH = 125

#Telemetry settings
#The satellite dictates the cadence (~2.4 s), so there is no polling interval.
#STALE_AFTER_S matches the receiver's own 8 s "listening..." heartbeat, so the
#dashboard and the firmware agree on when a link has gone quiet.
STALE_AFTER_S = 8.0

#A command waits for the next downlink before it can be transmitted (up to one
#cadence), and the satellite echoes the ack on the downlink after that — so a
#successful round trip takes about two packet periods, ~5 s. 8 s leaves room
#for one retransmission-free gap without reporting a false timeout.
COMMAND_TIMEOUT = 8.0

#How often to push link statistics to the dashboard, in seconds.
LINK_STATS_INTERVAL = 1.0

#Session logs land beside the range-test archives so Receiver/tools/analyze_range.py
#can read them with no extra configuration.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_LOG_DIR = Path(
    os.environ.get("TTC_LOG_DIR", PROJECT_ROOT / "Receiver" / "range_test_logs")
)
SESSION_LOGGING = os.environ.get("TTC_LOG_SESSIONS", "1") == "1"
