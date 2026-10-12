#Main Flask Backend for Cubesat TTC Ground Station
#
# Reads live telemetry off the ESP32 LoRa receiver over USB serial and streams
# it to the React dashboard over WebSocket. There is no simulated data path.

import itertools
import threading
import time

from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS

from config import (
    FLASK_HOST, FLASK_PORT, FLASK_DEBUG,
    SERIAL_PORT, SERIAL_BAUD, SERIAL_RETRY_INTERVAL,
    SESSION_LOG_DIR, SESSION_LOGGING,
    LINK_STATS_INTERVAL, COMMAND_TIMEOUT, STALE_AFTER_S,
    LORA_FREQUENCY, LORA_BANDWIDTH,
)
from link_stats import LinkStats
from serial_link import SerialLink
from session_logger import SessionLogger
from utils.parser import parse_csv_row, parse_comment, ACK_NAK

#Initialize Flask app
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

#Store latest state
latest_telemetry = {}
serial_state = {"state": "closed", "port": SERIAL_PORT, "detail": ""}

stats = LinkStats()
session_log = SessionLogger(SESSION_LOG_DIR, enabled=SESSION_LOGGING)

_last_stats_emit = 0.0

#Command in flight. The satellite acks by echoing the executed cmd_id in field
#"a" of its next downlink (-1 = NAK), so exactly one command is tracked at a
#time. cmd_id starts from the wall clock rather than 1: the satellite's ack is
#sticky for the rest of its session, so a backend restart must not reissue an id
#the satellite is still reporting from before the restart.
_command_ids = itertools.count(int(time.time()) % 30000 + 1)
_pending_lock = threading.Lock()
_pending_command = None


# ─────────────────────────────────────────────────────────────
# Command acknowledgement
# ─────────────────────────────────────────────────────────────
def _resolve_command(status: str, command_id=None, **extra):
    """Close out the in-flight command. Ignored if it was already resolved."""
    global _pending_command
    with _pending_lock:
        pending = _pending_command
        if pending is None:
            return
        if command_id is not None and pending["id"] != command_id:
            return
        _pending_command = None

    payload = {
        "status": status,
        "cmd_id": pending["id"],
        "cmd_type": pending["cmd_type"],
        "value": pending["value"],
        **extra,
    }
    print(f"[CMD] {status}: {payload}")
    socketio.emit("command_ack", payload)


def _command_timed_out(command_id):
    _resolve_command("timeout", command_id=command_id)


def _check_ack(packet: dict):
    """A downlink whose 'ack' matches the in-flight cmd_id confirms execution."""
    with _pending_lock:
        pending = _pending_command
    if not pending:
        return

    ack = packet.get("ack")
    # Only count acks carried by a packet received after the command went out.
    # The satellite latches its ack for the rest of its session, so a value that
    # predates the uplink says nothing about this command.
    if packet["rx_time"] <= pending["sent_at"]:
        return

    if ack == pending["id"]:
        _resolve_command("executed", command_id=pending["id"], p_id=packet.get("p_id"))
    elif ack == ACK_NAK:
        # The satellite heard something and rejected it — bad CRC on the uplink
        # or an invalid param. Distinct from a timeout, where nothing arrived.
        _resolve_command("nak", command_id=pending["id"], p_id=packet.get("p_id"),
                         detail="satellite rejected the command (CRC or param)")


# ─────────────────────────────────────────────────────────────
# Serial ingest
# ─────────────────────────────────────────────────────────────
def _emit_link_stats(force=False):
    global _last_stats_emit
    now = time.time()
    if not force and now - _last_stats_emit < LINK_STATS_INTERVAL:
        return
    _last_stats_emit = now
    socketio.emit("link_stats", stats.snapshot())


def on_serial_line(line: str):
    """Called for every newline-terminated line the receiver prints."""
    global latest_telemetry

    packet = parse_csv_row(line)
    if packet is not None:
        latest_telemetry = packet
        stats.add_packet(
            packet["p_id"], packet["rssi"], packet["snr"], packet["crc_ok"]
        )
        session_log.write(line)
        socketio.emit("telemetry", packet)
        _check_ack(packet)
        _emit_link_stats()
        return

    event = parse_comment(line)
    if event is None:
        return

    if event["kind"] == "parse_fail":
        stats.add_parse_fail()

    socketio.emit("link_event", event)
    _emit_link_stats()


def on_serial_status(status: dict):
    global serial_state
    serial_state = status
    print(f"[SERIAL] {status['state']} {status['port']} {status['detail']}".rstrip())
    socketio.emit("serial_status", status)


serial_link = SerialLink(
    port=SERIAL_PORT,
    baud=SERIAL_BAUD,
    on_line=on_serial_line,
    on_status=on_serial_status,
    retry_interval=SERIAL_RETRY_INTERVAL,
)


# ─────────────────────────────────────────────────────────────
# REST
# ─────────────────────────────────────────────────────────────
def station_config() -> dict:
    """Values the dashboard would otherwise have to hardcode."""
    return {
        "stale_after_s": STALE_AFTER_S,
        "command_timeout_s": COMMAND_TIMEOUT,
        "lora": {"frequency_mhz": LORA_FREQUENCY, "bandwidth_khz": LORA_BANDWIDTH},
    }


@app.route('/')
def index():
    return {
        "status": "CubeSat TTC Ground Station Running",
        "serial": serial_state,
        "config": station_config(),
        "packets": stats.good,
    }


@app.route('/telemetry/latest')
def get_latest_telemetry():
    return latest_telemetry if latest_telemetry else {"error": "No telemetry received yet"}


@app.route('/link/stats')
def get_link_stats():
    return stats.snapshot()


# ─────────────────────────────────────────────────────────────
# WebSocket
# ─────────────────────────────────────────────────────────────
@socketio.on('connect')
def on_connect():
    print("[BACKEND] React dashboard connected")
    # Hand the new client the current picture so a page refresh is not blank
    # until the next packet arrives ~2.4 s later.
    socketio.emit('station_config', station_config())
    socketio.emit('serial_status', serial_state)
    socketio.emit('link_stats', stats.snapshot())
    if latest_telemetry:
        socketio.emit('telemetry', latest_telemetry)


@socketio.on('disconnect')
def on_disconnect():
    print("[BACKEND] React dashboard disconnected")


@socketio.on('command')
def on_command(data):
    global _pending_command

    try:
        cmd_type = int(data.get("cmd_type"))
        value = int(data.get("value", 0))
    except (AttributeError, TypeError, ValueError):
        socketio.emit('command_ack', {"status": "rejected", "detail": "malformed command"})
        return

    if serial_link.state != "open":
        socketio.emit('command_ack', {
            "status": "failed", "cmd_type": cmd_type, "value": value,
            "detail": "serial port not open",
        })
        return

    # A second command while one is in flight replaces it: the receiver only
    # holds one pending uplink, so tracking more here would report acks that
    # can never arrive.
    _resolve_command("superseded")

    command_id = next(_command_ids)
    with _pending_lock:
        _pending_command = {
            "id": command_id,
            "cmd_type": cmd_type,
            "value": value,
            "sent_at": time.time(),
        }

    if not serial_link.send_command(command_id, cmd_type, value):
        _resolve_command("failed", command_id=command_id, detail="serial write failed")
        return

    print(f"[CMD] uplink queued: id={command_id} ct={cmd_type} param={value}")
    socketio.emit('command_ack', {
        "status": "pending", "cmd_id": command_id,
        "cmd_type": cmd_type, "value": value,
    })
    threading.Timer(COMMAND_TIMEOUT, _command_timed_out, args=(command_id,)).start()


if __name__ == '__main__':
    serial_link.start()
    print(f"[BACKEND] Serial link on {SERIAL_PORT} @ {SERIAL_BAUD}")
    print(f"[BACKEND] Starting Flask server on port {FLASK_PORT}")
    try:
        # The reloader would run this block twice and the second process would
        # fail to open the port, so it stays off even in debug.
        socketio.run(app, host=FLASK_HOST, port=FLASK_PORT,
                     debug=FLASK_DEBUG, use_reloader=False)
    finally:
        serial_link.stop()
        session_log.close()
