#Main Flask Backend for Cubesat TTC Ground Station

from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS
import threading
from config import FLASK_HOST, FLASK_PORT, FLASK_DEBUG, TELEMETRY_UPDATE_INTERVAL, MOCK_MODE
from utils.parser import parse_telemetry
from mock_serial import mock_serial_stream

#Initialize Flask app
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

#Store Latest telemetry 
latest_telemetry = {}


def telemetry_thread():
    """
    Background thread that reads telemetry and emits via WebSocket.
    """
    if MOCK_MODE:
        print("[BACKEND] Running in MOCK mode - using simulated data")
        for raw_packet in mock_serial_stream(TELEMETRY_UPDATE_INTERVAL):
            parsed = parse_telemetry(raw_packet)
            if parsed:
                global latest_telemetry
                latest_telemetry = parsed
                socketio.emit('telemetry', parsed)
                print(f"[BACKEND] Emitted p_id={parsed['p_id']}")


@app.route('/')
def index():
    return {"status": "CubeSat TTC Ground Station Running", "mock_mode": MOCK_MODE}


@app.route('/telemetry/latest')
def get_latest_telemetry():
    return latest_telemetry if latest_telemetry else {"error": "No telemetry received yet"}


@socketio.on('connect')
def on_connect():
    print("[BACKEND] React dashboard connected")


@socketio.on('disconnect')
def on_disconnect():
    print("[BACKEND] React dashboard disconnected")


@socketio.on('command')
def on_command(data):
    print(f"[BACKEND] Command received: {data}")
    # TODO: Forward command to ESP32 via serial when hardware is ready
    socketio.emit('command_ack', {"status": "received", "command": data})


if __name__ == '__main__':
    #Start telemetry thread
    thread = threading.Thread(target=telemetry_thread, daemon=True)
    thread.start()
    print(f"[BACKEND] Starting Flask server on port {FLASK_PORT}")
    socketio.run(app, host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG)