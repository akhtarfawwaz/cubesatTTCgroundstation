#config.py - Ground Station Configuration

#Serial port settings 
SERIAL_PORT = "COM3"
SERIAL_BAUD = 115200

#Flask server settings
FLASK_HOST = "0.0.0.0"
FLASK_PORT = 5000
FLASK_DEBUG = True

#LoRa settings
LORA_FREQUENCY = 433
LORA_BANDWIDTH = 125

#Telemetry settings 
TELEMETRY_UPDATE_INTERVAL = 2.0
COMMAND_TIMEOUT = 3.0

#Mock mode
MOCK_MODE = True