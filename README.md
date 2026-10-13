# LoRa-Based TTC Subsystem and Ground Station for a 1U CubeSat Prototype

Source code, firmware, session logs and analysis scripts for the undergraduate thesis

> Akhtar Fawwaz Hannan (22/497801/PA/21452), *Design and Implementation of LoRa-Based Telemetry, Tracking, and Command (TTC) Subsystem for 1U CubeSat Prototype as an Educational Tool*, Electronics and Instrumentation, FMIPA, Universitas Gadjah Mada, 2026.

The version cited in the thesis is the Git tag **`v1.0-thesis`**.

## Repository layout

| Path | Contents |
|---|---|
| `Receiver/src/main.cpp` | Single firmware source for both radios. Build flag `IS_TRANSMITTER` gives the CubeSat role, `IS_RECEIVER` the ground-station receiver role. |
| `Receiver/platformio.ini` | PlatformIO environments `transmitter` and `receiver`, with pinned library versions. |
| `Receiver/tools/range_logger.py` | Serial logger used for the 10 m and 50 m range tests. |
| `Receiver/tools/analyze_range.py` | Produces Table 5.4 and Figures 5.6–5.7 of the thesis from the two range-test logs. |
| `Receiver/tools/analyze_sessions.py` | Summarises every session log (PDR with 95 % confidence interval, CRC failures, command ACK/NAK events, cadence, RSSI/SNR, GNSS fix share). |
| `Receiver/range_test_logs/` | All raw data: the two range-test logs and every dashboard session log. |
| `backend/` | Flask + Flask-SocketIO ground-station backend (serial ingest, parser, session logger, link statistics, command tracking). |
| `frontend/` | React + Vite dashboard. |

## Software versions

| Component | Version |
|---|---|
| PlatformIO platform | `espressif32`, board `esp32dev`, framework `arduino` |
| LoRa library | `sandeepmistry/LoRa ^0.8.0` |
| ArduinoJson | `bblanchon/ArduinoJson ^6.21.3` |
| Sensors (transmitter) | Adafruit BMP280 ^2.6.8, I2Cdevlib-MPU6050 ^1.0.0, TinyGPSPlus ^1.0.3, Arduino Madgwick ^1.2.0 |
| Backend | Python 3, Flask 3.1.3, Flask-SocketIO 5.6.1, pyserial 3.5 (full list in `backend/requirements.txt`) |
| Frontend | React 19.2, Vite 8.0, Recharts 3.8, socket.io-client 4.8 (full list in `frontend/package.json`) |

## Radio and protocol parameters

* 433 MHz, SF7, 125 kHz bandwidth, coding rate 4/5, 8-symbol preamble, 17 dBm, explicit header. The SX127x payload CRC is **not** enabled; integrity is checked only by the application CRC below.
* Integrity check: CRC-16/CCITT-FALSE (polynomial 0x1021, initial value 0xFFFF, no reflection, no final XOR), computed over the compact JSON serialization of the packet without the `c` field and sent as four upper-case hexadecimal characters in `c`. The CRC detects errors; it provides no encryption or authentication.
* Timing: one telemetry frame every 2000 ms, followed immediately by a 500 ms command window. Polling for an uplink starts 150 ms into the window.

## How to run

```bash
# firmware (one board at a time)
cd Receiver
pio run -e transmitter -t upload
pio run -e receiver -t upload

# backend
cd backend
python -m venv venv && venv\Scripts\activate      # Windows; use source venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python app.py                                      # serial port: COM3 by default, or set TTC_SERIAL_PORT

# dashboard
cd frontend
npm install
npm run dev                                        # http://localhost:5173
```

## Data

Every file in `Receiver/range_test_logs/` is a CSV with one row per received frame. The header is:

```
rx_ms,p_id,rssi,snr,crc,ts,mode,temp,press,alt,ax,ay,az,gx,gy,gz,roll,pitch,yaw,lat,lon,gps_alt,sats,volt,err,ack,alt_kf
```

The two range-test files (`10m data.csv`, `50m data.csv`) have 26 columns and no `alt_kf`.

### Timestamps

There are three different clocks in the data. Do not mix them in one time series.

| Field | Clock | Meaning |
|---|---|---|
| `rx_ms` | Ground-station receiver | Milliseconds since the receiver ESP32 booted. Used for cadence. |
| `ts` | CubeSat | Unix epoch seconds (UTC) after the first GNSS fix, then kept running from the CubeSat clock. Before any fix: seconds since the CubeSat booted. |
| File name `session_YYYYMMDD_HHMMSS` | Ground-station laptop | Local time (WIB, UTC+7) at which the session log was opened. |

### Session timeline

| Date (WIB) | File | Setup and purpose | Used in thesis |
|---|---|---|---|
| 12 Aug 2026, before 10:50 | `10m data.csv`, `50m data.csv` | Outdoor line-of-sight range test, original antenna installation, blocking firmware loop (2.465 s cadence). The 100 m run received no frames, so no file exists. | Section 5.5, Table 5.4 |
| 12 Aug 2026 | `session_20260812_123057.csv`, `session_20260812_123149.csv` | Bench sessions on the blocking firmware loop. | Session summary |
| 16 Aug 2026 | `session_20260816_171600.csv` | Polled firmware (2.000 s). Command tests including the Mode Switch with cmd_id 77; contains the one CRC failure. | Section 5.7, session summary |
| 17 Aug 2026 | `session_20260817_100457.csv` | 7-hour stability session with commands. | Session summary |
| 24 Aug 2026, 14:43 | `session_20260824_144349.csv` | Outdoor session after the antenna installation was corrected; external GNSS antenna; 4,201 frames. | Sections 5.6 and 5.8 |
| 24 Aug 2026, 17:04 | `session_20260824_170432.csv` | Short follow-up session. | Session summary |
| 28 Sep 2026 | `session_20260928_*.csv` | Command and dashboard checks. | Session summary |
| 6 Oct 2026 | `session_20261006_170710.csv` | Command and dashboard checks. | Session summary |

| 12 Oct 2026, 11:43–14:05 | `run_{distance}m_r{n}_*.csv`, `summary_100pkt.csv` | Repeated range test, 2 runs × 100 transmitted packets at 10, 50 and 100 m, corrected antenna installation (vertical), sunny, line of sight. Logger starts stopped within the first minute because the distance or run number was entered wrongly are not included. | Section 5.5.1, Table 5.6 |
| 12 Oct 2026, 12:44–12:56 | `fault_test_*.csv` | Command fault tests (invalid, malformed, wrong CRC, duplicate, no satellite), 3 repetitions each, receiver built with `-D FAULT_TEST`. | Section 5.7, Table 5.9 |

Separations for the bench and dashboard sessions were not recorded. Only the two range-test files are controlled distance measurements.

### Reproducing the thesis numbers

```bash
cd Receiver/tools
python analyze_range.py          # Table 5.4, Figures 5.6-5.7
python analyze_sessions.py       # session summary table (all sessions)
```
