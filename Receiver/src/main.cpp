#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>
#include <ArduinoJson.h>

// ─────────────────────────────────────────────────────────────
// Pin definitions — ICD TTC-ICD-001 v1.0
// ─────────────────────────────────────────────────────────────
#ifdef IS_TRANSMITTER
  #include <Wire.h>
  #include <Adafruit_BMP280.h>
  #include <MPU6050.h>
  #include <TinyGPSPlus.h>
  #include <HardwareSerial.h>
  #include <MadgwickAHRS.h>

  #define LORA_NSS   5
  #define LORA_RST   27
  #define LORA_DIO0  26

  #define SDA_PIN    21
  #define SCL_PIN    22
  #define BMP280_ADDR 0x76

  // The two pins the GPS UART is strapped to. Which is which is decided at
  // runtime — see gps_probe(). Davino's schematic (Figure 4.1) routes the
  // module's TX to GPIO17, while his firmware opens the port with GPIO16 as
  // the ESP32's receive pin; the two contradict each other, and a hand-wired
  // prototype may follow either.
  #define GPS_PIN_A  16
  #define GPS_PIN_B  17

  #define BUZZER_PIN 4    // CMD_BUZZER — passive buzzer, ledc channel 0
  #define VBAT_PIN   35   // battery divider tap, see HAS_VBAT_DIVIDER
#endif

#ifdef IS_RECEIVER
  #define LORA_NSS   5
  #define LORA_RST   14
  #define LORA_DIO0  2
#endif

// ─────────────────────────────────────────────────────────────
// LoRa RF parameters — ICD Section 5
// ─────────────────────────────────────────────────────────────
#define LORA_FREQ      433E6
#define LORA_SF        7
#define LORA_BW        125E3
#define LORA_CR        5
#define LORA_PREAMBLE  8

// SX127x explicit-header mode caps a packet at 255 B and the LoRa library
// TRUNCATES SILENTLY past that (endPacket() still returns true). Stay under
// it with room for the CRC field that gets appended after the size check.
#define LORA_MAX_PAYLOAD 250
#define CRC_FIELD_LEN    11   // ,"c":"XXXX"

// ─────────────────────────────────────────────────────────────
// err bitfield — reported in field "e"
// ─────────────────────────────────────────────────────────────
#define ERR_BMP      0x01
#define ERR_MPU      0x02
#define ERR_GPS      0x04
#define ERR_OVERSIZE 0x08

// ─────────────────────────────────────────────────────────────
// Uplink command frame — ICD Section 6 / thesis Table 4.8
//   {"id":<cmd_id>,"ct":<cmd_type>,"pm":<param>,"c":"XXXX"}
// Same CRC-16 as the downlink, computed over the frame without "c".
// On success the satellite latches cmd_id into downlink field "a" (ACK);
// a CRC failure or an invalid param latches -1 (NAK). 0 means "no command
// received this session".
// ─────────────────────────────────────────────────────────────
#define CMD_SET_MODE   1   // pm: 2 = Normal, 1 = Safe
#define CMD_CALIBRATE  2   // pm: 0
#define CMD_BUZZER     3   // pm: beep duration in ms

#define ACK_NAK       -1

// ─────────────────────────────────────────────────────────────
// CRC-16 CCITT
// ─────────────────────────────────────────────────────────────
uint16_t compute_crc16(const uint8_t* data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; i++) {
    crc ^= (uint16_t)data[i] << 8;
    for (int j = 0; j < 8; j++) {
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : (crc << 1);
    }
  }
  return crc;
}

// ═════════════════════════════════════════════════════════════
//  TRANSMITTER — CubeSat side
// ═════════════════════════════════════════════════════════════
#ifdef IS_TRANSMITTER

// The battery telemetry is only real if a divider is actually fitted on
// VBAT_PIN. With the pin floating the ADC returns noise, which is worse than an
// honest placeholder — so this stays 0 until the hardware is there.
// Wiring to fit before setting this to 1 — a 1S LiPo peaks at 4.2 V and the
// ESP32 ADC tops out at 3.3 V, so the battery must be divided down:
//
//   BAT+ ──[ R1 10k ]──┬── GPIO35 (ADC1_CH7, input-only)
//                      │
//                     [ R2 10k ]   plus 100 nF to GND for noise
//                      │
//   BAT- ──────────────┴── GND   (must share ground with the ESP32)
//
// Equal resistors halve the voltage, so VBAT_RATIO is 2.0 and a full 4.2 V
// battery reads 2.1 V at the pin — inside the region where the ADC is linear.
// Use ADC1 pins only. 10k/10k draws ~210 uA, negligible against a 1300 mAh cell.
#define HAS_VBAT_DIVIDER 0
#define VBAT_RATIO       2.0f    // (R1 + R2) / R2

// ── Loop timing ──
// The old build did all of its work inside one blocking pass: transmit, spin
// 500 ms in the RX window, delay(1500). That left the GPS UART unread for
// almost two seconds at a stretch. A NEO-6M emits ~600 B/s of NMEA and the
// ESP32 RX FIFO is 128 B, so every sentence was arriving corrupted and
// TinyGPS++ never saw a complete fix — which is exactly the symptom that was
// being blamed on the GPS module. Everything below is polled instead.
#define TELEMETRY_INTERVAL_MS 2000
#define RX_WINDOW_MS          500
// Do not poll the radio for the first RX_GUARD_MS of the window. send_telemetry()
// leaves it in RX_CONTINUOUS, but LoRa::parsePacket() rewrites REG_OP_MODE to
// RX_SINGLE on every call that does not find a completed packet (LoRa.cpp, the
// "not currently in RX mode" branch). That register write restarts the receiver
// and destroys any reception already in progress — and the uplink is always in
// progress right here, because the ground station fires it the instant this
// packet lands. Staying quiet lets RX_CONTINUOUS capture the frame into the FIFO
// and raise RxDone; the first poll after the guard then finds it waiting.
// The uplink is ~40 B, about 82 ms on air at SF7/125 kHz, so 150 ms covers it
// with margin and still leaves 70 % of the window for ordinary polling.
#define RX_GUARD_MS           150
#define IMU_PERIOD_MS         20     // 50 Hz — the Madgwick sample rate
#define IMU_SAMPLE_HZ         50.0f
#define GPS_RX_BUFFER         1024
#define GPS_PROBE_MS          4000   // silence on a pin before trying the other

// ── Kalman filter — altitude fusion ──
#define KALMAN_Q  0.01f   // process noise: how fast altitude may really change
#define KALMAN_R  0.1f    // measurement noise: barometer jitter

Adafruit_BMP280 bmp;
MPU6050 mpu;
TinyGPSPlus gps;
HardwareSerial gpsSerial(2);
Madgwick madgwick;

uint32_t p_id = 1;
int32_t  last_ack   = 0;   // cmd_id of the last executed command, or ACK_NAK
int32_t  last_cmd_id = 0;  // dedupe: the ground may retransmit one cmd_id
uint8_t  current_mode = 2; // 2 = Normal, 1 = Safe
uint8_t  sensor_err = 0;   // sticky BMP/MPU flags set during setup

// Latest IMU sample, bias corrected. Held as globals because the 50 Hz sampler
// and the 0.5 Hz packet builder are now separate concerns.
float ax = 0, ay = 0, az = 0;   // g
float gx = 0, gy = 0, gz = 0;   // deg/s
float gx_bias = 0, gy_bias = 0, gz_bias = 0;

// Kalman state — fused altitude
float kalman_alt = 0.0f;
float kalman_vel = 0.0f;
float kalman_p   = 1.0f;
bool  kalman_ready = false;

// Unix epoch, recovered from the GPS. Once a fix has been seen the offset keeps
// the clock running off millis() through subsequent GPS dropouts, so ts does
// not fall back to uptime seconds the moment the satellite count dips.
uint32_t epoch_offset = 0;
bool     have_epoch   = false;

unsigned long buzz_until_ms = 0;
bool          buzzing       = false;

unsigned long last_tx_ms  = 0;
unsigned long last_imu_ms = 0;
unsigned long rx_open_ms  = 0;
bool          rx_open     = false;

// ── GPS ─────────────────────────────────────────────────────
// Called from every loop pass. Half the fix for the dead GPS: the UART must be
// drained faster than the module fills it. The other half is gps_probe(),
// below — draining the wrong pin fast changes nothing.
void gps_pump() {
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }
}

int  gps_rx_pin = GPS_PIN_A;
int  gps_tx_pin = GPS_PIN_B;
bool gps_pins_found   = false;
unsigned long gps_probe_start = 0;

void gps_open(int rx, int tx) {
  gpsSerial.end();
  gps_rx_pin = rx;
  gps_tx_pin = tx;
  // A 1 kB buffer holds ~1.7 s of NMEA at 9600 baud, so even a pass that runs
  // long cannot lose a sentence before gps_pump() gets to it.
  gpsSerial.setRxBufferSize(GPS_RX_BUFFER);
  gpsSerial.begin(9600, SERIAL_8N1, rx, tx);
  gps_probe_start = millis();
}

// Settle the TX/RX orientation by experiment instead of by assumption. If no
// byte has arrived after GPS_PROBE_MS, the receive pin is wired to the module's
// RX input — nothing drives it — so swap and listen on the other one. Costs a
// few seconds at boot and removes a reflash from the diagnosis loop.
void gps_probe() {
  if (gps_pins_found) return;

  if (gps.charsProcessed() > 0) {
    gps_pins_found = true;
    Serial.printf("[GPS] receiving on GPIO %d (module TX -> GPIO %d)\n",
                  gps_rx_pin, gps_rx_pin);
    return;
  }
  if (millis() - gps_probe_start < GPS_PROBE_MS) return;

  int next_rx = (gps_rx_pin == GPS_PIN_A) ? GPS_PIN_B : GPS_PIN_A;
  int next_tx = (next_rx == GPS_PIN_A) ? GPS_PIN_B : GPS_PIN_A;
  Serial.printf("[GPS] silent on GPIO %d for %d ms — trying GPIO %d\n",
                gps_rx_pin, GPS_PROBE_MS, next_rx);
  gps_open(next_rx, next_tx);
}

// Ported from monitor_gps_health() in the OBC reference. Without it "the GPS
// does not work" is undiagnosable from the downlink alone: a miswired UART and
// a module that simply cannot see sky produce an identical NO-FIX row.
void gps_health() {
  static unsigned long last = 0;
  if (millis() - last < 10000) return;
  last = millis();

  unsigned long chars = gps.charsProcessed();
  Serial.printf("[GPS] rx_pin=%d chars=%lu sentences_with_fix=%lu csum_err=%lu sats=%d fix=%s\n",
                gps_rx_pin, chars, gps.sentencesWithFix(), gps.failedChecksum(),
                gps.satellites.isValid() ? (int)gps.satellites.value() : -1,
                gps.location.isValid() ? "yes" : "no");

  if (chars == 0) {
    Serial.println("[GPS]   silent on BOTH pins — power or a dead module, not orientation.");
    Serial.println("[GPS]   check the module has 3V3 and a shared ground with the ESP32.");
  } else if (gps.sentencesWithFix() == 0) {
    Serial.println("[GPS]   NMEA is arriving but carries no fix — antenna or sky view.");
    Serial.println("[GPS]   cold start needs 30-60 s outdoors; indoors it never locks.");
  }
}

// Days-from-civil (Howard Hinnant's algorithm) — TinyGPS++ hands back a
// calendar date, but ICD field "t" and thesis Table 4.7 want a uint32 Unix
// epoch, not a formatted string or seconds-of-day.
uint32_t civil_to_epoch(int y, int m, int d, int hh, int mm, int ss) {
  y -= (m <= 2);
  int      era = (y >= 0 ? y : y - 399) / 400;
  unsigned yoe = (unsigned)(y - era * 400);
  unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
  unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
  long     days = (long)era * 146097 + (long)doe - 719468;
  return (uint32_t)(days * 86400L + hh * 3600L + mm * 60L + ss);
}

// ── IMU ─────────────────────────────────────────────────────
void imu_sample() {
  int16_t ax_r, ay_r, az_r, gx_r, gy_r, gz_r;
  mpu.getMotion6(&ax_r, &ay_r, &az_r, &gx_r, &gy_r, &gz_r);

  ax = ax_r / 16384.0f;          // ±2 g full scale
  ay = ay_r / 16384.0f;
  az = az_r / 16384.0f;
  gx = gx_r / 131.0f - gx_bias;  // ±250 deg/s full scale
  gy = gy_r / 131.0f - gy_bias;
  gz = gz_r / 131.0f - gz_bias;

  // The Arduino Madgwick library wants deg/s and g, and integrates assuming it
  // is called at the rate passed to begin(). Feeding it once per downlink (0.5
  // Hz) would make the attitude converge a hundred times too slowly, which is
  // why this sits in the 50 Hz path and not in the packet builder.
  madgwick.updateIMU(gx, gy, gz, ax, ay, az);
}

// Averages the gyro at rest and stores the offset. Ported from the OBC's
// imu.setBias() — the MPU6050 driver used here has no equivalent built in.
// Runs at boot and again on CMD_CALIBRATE.
void calibrate_gyro(int samples = 100) {
  if (sensor_err & ERR_MPU) return;

  long sx = 0, sy = 0, sz = 0;
  int16_t ax_r, ay_r, az_r, gx_r, gy_r, gz_r;
  for (int i = 0; i < samples; i++) {
    mpu.getMotion6(&ax_r, &ay_r, &az_r, &gx_r, &gy_r, &gz_r);
    sx += gx_r; sy += gy_r; sz += gz_r;
    delay(2);
    gps_pump();   // still the only thing keeping the NMEA stream intact
  }
  gx_bias = (sx / (float)samples) / 131.0f;
  gy_bias = (sy / (float)samples) / 131.0f;
  gz_bias = (sz / (float)samples) / 131.0f;
  Serial.printf("[IMU] gyro bias %.2f / %.2f / %.2f deg/s (%d samples)\n",
                gx_bias, gy_bias, gz_bias, samples);
}

// ── Kalman ──────────────────────────────────────────────────
float kalman_update(float measurement, float dt) {
  // Predict
  kalman_alt += kalman_vel * dt;
  kalman_p   += KALMAN_Q;

  // Update
  float gain       = kalman_p / (kalman_p + KALMAN_R);
  float innovation = measurement - kalman_alt;
  kalman_alt += gain * innovation;
  kalman_p   *= (1.0f - gain);

  // The OBC reference leaves velocity at zero forever, which makes the predict
  // step a no-op and reduces the filter to a plain low-pass. Estimating it from
  // the correction actually applied lets the filter lead a real climb instead
  // of lagging it.
  if (dt > 0.0f) {
    kalman_vel = 0.8f * kalman_vel + 0.2f * (gain * innovation) / dt;
  }
  return kalman_alt;
}

// ── Buzzer ──────────────────────────────────────────────────
// Non-blocking: CMD_BUZZER only sets an end time. A delay() here would stall
// the GPS drain and the attitude filter for the whole beep.
void buzzer_start(uint32_t ms) {
  if (ms == 0 || ms > 5000) ms = 200;   // clamp — param is operator supplied
  ledcWriteTone(0, 2000);
  buzz_until_ms = millis() + ms;
  buzzing = true;
}

void buzzer_service() {
  if (buzzing && (long)(millis() - buzz_until_ms) >= 0) {
    ledcWriteTone(0, 0);
    buzzing = false;
  }
}

// ── Battery ─────────────────────────────────────────────────
float read_battery() {
#if HAS_VBAT_DIVIDER
  // analogReadMilliVolts(), not analogRead(). The ESP32's raw ADC is markedly
  // non-linear and varies chip to chip; the milliVolts call applies the
  // per-chip calibration burned into eFuse at the factory, which is the
  // difference between roughly +-150 mV and roughly +-20 mV of error. A
  // 10-sample average on top of that settles the remaining jitter.
  uint32_t mv = 0;
  for (int i = 0; i < 10; i++) mv += analogReadMilliVolts(VBAT_PIN);
  return (mv / 10.0f) * VBAT_RATIO / 1000.0f;
#else
  return 3.70f;   // no divider fitted — declared placeholder, not a reading
#endif
}

// ── Command handling ────────────────────────────────────────
// The uplink is unauthenticated and the RF layer gives no delivery guarantee,
// so a corrupted frame must never be acted on: verify the CRC exactly the way
// the downlink does. Rejects latch NAK rather than staying silent, so the
// ground station can tell "command was corrupted" from "command never arrived".
void handle_command(const String& frame) {
  StaticJsonDocument<128> doc;
  if (deserializeJson(doc, frame)) {
    Serial.printf("[CMD] NAK — not JSON: %s\n", frame.c_str());
    last_ack = ACK_NAK;
    return;
  }

  String crc_received = doc["c"] | "";
  doc.remove("c");
  String check;
  serializeJson(doc, check);
  char crc_calc[5];
  sprintf(crc_calc, "%04X",
          compute_crc16((const uint8_t*)check.c_str(), check.length()));

  if (crc_received != String(crc_calc)) {
    Serial.printf("[CMD] NAK — CRC %s != %s\n", crc_received.c_str(), crc_calc);
    last_ack = ACK_NAK;
    return;
  }

  int32_t id = doc["id"] | 0;
  int     ct = doc["ct"] | 0;
  int     pm = doc["pm"] | 0;

  // The ack is sticky for the rest of the session, so the ground station
  // retransmits a command it has not seen acked yet. Re-acking without
  // re-executing keeps a repeated calibration or beep from firing twice.
  if (id != 0 && id == last_cmd_id) {
    Serial.printf("[CMD] duplicate id=%ld — re-acked, not re-executed\n", (long)id);
    last_ack = id;
    return;
  }

  switch (ct) {
    case CMD_SET_MODE:
      if (pm != 1 && pm != 2) {
        Serial.printf("[CMD] NAK — bad mode %d\n", pm);
        last_ack = ACK_NAK;
        return;
      }
      current_mode = (uint8_t)pm;
      Serial.printf("[CMD] mode -> %s\n", pm == 2 ? "NORMAL" : "SAFE");
      break;

    case CMD_CALIBRATE:
      Serial.println("[CMD] recalibrating gyro bias...");
      calibrate_gyro();
      break;

    case CMD_BUZZER:
      Serial.printf("[CMD] beep %d ms\n", pm);
      buzzer_start((uint32_t)pm);
      break;

    default:
      Serial.printf("[CMD] NAK — unknown ct %d\n", ct);
      last_ack = ACK_NAK;
      return;
  }

  last_cmd_id = id;
  last_ack    = id;   // ACK: thesis Table 4.7, ack echoes cmd_id
}

// ── Telemetry ───────────────────────────────────────────────
void send_telemetry(float dt_s) {
  uint8_t err_flags = sensor_err;

  // BMP280
  float temp  = bmp.readTemperature();
  float press = bmp.readPressure();
  float alt   = bmp.readAltitude(1013.25);

  // GPS — time.isValid() goes true on the first parsed sentence even with no
  // fix, so gate everything on satellite count or ts reads back as 0. age()
  // guards against a fix that went stale minutes ago still looking current.
  bool gps_fix = gps.location.isValid() && gps.location.age() < 2000
                 && gps.satellites.isValid() && gps.satellites.value() > 0;
  if (!gps_fix) err_flags |= ERR_GPS;

  double lat     = gps_fix ? gps.location.lat() : -7.7956;
  double lon     = gps_fix ? gps.location.lng() : 110.3695;
  float  gps_alt = (gps_fix && gps.altitude.isValid()) ? gps.altitude.meters() : 0.0f;
  int    sats    = gps.satellites.isValid() ? (int)gps.satellites.value() : 0;

  // Timestamp — Unix epoch (uint32), thesis Table 4.7.
  if (gps_fix && gps.date.isValid() && gps.time.isValid() && gps.date.year() > 2000) {
    uint32_t epoch = civil_to_epoch(gps.date.year(), gps.date.month(), gps.date.day(),
                                    gps.time.hour(), gps.time.minute(), gps.time.second());
    epoch_offset = epoch - (uint32_t)(millis() / 1000);
    have_epoch   = true;
  }
  uint32_t ts = have_epoch ? epoch_offset + (uint32_t)(millis() / 1000)
                           : (uint32_t)(millis() / 1000);

  // Altitude fusion. The barometer is the primary source — it resolves ~8 cm
  // per Pa where the GPS altitude is good to several metres on a good day — so
  // the GPS only gets a 20 % vote, and only when it actually has a fix.
  float alt_meas = gps_fix ? (alt * 0.8f + gps_alt * 0.2f) : alt;
  if (!kalman_ready) {
    kalman_alt   = alt_meas;
    kalman_ready = true;
  }
  float alt_kf = kalman_update(alt_meas, dt_s);

  float volt = read_battery();

  // ── Build JSON payload — short keys, ICD TTC-ICD-001 v1.1 ──
  // Precision is trimmed to what the sensors actually resolve: 1 Pa is ~8 cm
  // of altitude, and 0.1 deg/s is well under the MPU6050 noise floor.
  StaticJsonDocument<512> doc;
  doc["i"]  = p_id;                        // p_id
  doc["t"]  = ts;                          // timestamp, Unix epoch
  doc["m"]  = current_mode;                // mode
  doc["tp"] = round(temp  * 10) / 10.0;    // temp    °C
  doc["pr"] = (int32_t)round(press);       // press   Pa
  doc["al"] = round(alt   * 10) / 10.0;    // alt     m, raw barometric
  doc["af"] = round(alt_kf * 10) / 10.0;   // alt_kf  m, Kalman fused
  doc["ax"] = round(ax    * 100) / 100.0;  // g
  doc["ay"] = round(ay    * 100) / 100.0;
  doc["az"] = round(az    * 100) / 100.0;
  doc["gx"] = round(gx    * 10) / 10.0;    // °/s
  doc["gy"] = round(gy    * 10) / 10.0;
  doc["gz"] = round(gz    * 10) / 10.0;
  doc["r"]  = round(madgwick.getRoll()  * 10) / 10.0;   // Madgwick AHRS
  doc["p"]  = round(madgwick.getPitch() * 10) / 10.0;
  doc["y"]  = round(madgwick.getYaw()   * 10) / 10.0;
  // Rounded to 6 dp (≈11 cm) BEFORE serialization, not just conceptually.
  // TinyGPS++ hands back a full-precision double and ArduinoJson renders it to
  // 9 significant digits, so an unrounded fix costs 4 extra bytes per
  // coordinate — and the packet has no room to spare. See thesis §4.6.2.
  doc["la"] = round(lat * 1000000.0) / 1000000.0;
  doc["lo"] = round(lon * 1000000.0) / 1000000.0;
  doc["ga"] = round(gps_alt * 10) / 10.0;  // gps_alt m
  doc["s"]  = sats;
  doc["v"]  = round(volt * 100) / 100.0;   // volt V
  doc["e"]  = err_flags;
  doc["a"]  = last_ack;

  String payload;
  serializeJson(doc, payload);

  // Oversize guard. Typical packet is ~170 B; this only ever fires if every
  // field lands on its longest form at once. roll/pitch/yaw are recoverable —
  // the receiver defaults them back to 0 — and are dropped first.
  if (payload.length() + CRC_FIELD_LEN > LORA_MAX_PAYLOAD) {
    err_flags |= ERR_OVERSIZE;
    doc["e"] = err_flags;
    doc.remove("r");
    doc.remove("p");
    doc.remove("y");
    payload = "";
    serializeJson(doc, payload);
    Serial.printf("[WARN] payload trimmed to %u B\n", payload.length());
  }

  // Hitung CRC-16 dari byte array
  uint16_t crc = compute_crc16((const uint8_t*)payload.c_str(), payload.length());
  char crc_str[5];
  sprintf(crc_str, "%04X", crc);
  doc["c"] = crc_str;

  // Final serialize dengan CRC
  String packet;
  serializeJson(doc, packet);

  // Last line of defence — never hand the radio something it will truncate.
  if (packet.length() > 255) {
    LoRa.receive();
    Serial.printf("[ERROR] packet %u B exceeds radio limit, dropped\n",
                  packet.length());
    return;
  }

  LoRa.beginPacket();
  LoRa.print(packet);
  bool sent = LoRa.endPacket();

  // Listen FIRST, report second — this ordering is load bearing.
  //
  // The ground station fires its uplink within a few ms of this packet landing,
  // and a LoRa receiver can only latch onto a packet if it is already listening
  // when the preamble arrives. The preamble is 8 symbols, about 8.2 ms at SF7.
  // Printing the packet costs ~22 ms at 115200 baud (250 bytes, 10 bits each),
  // so a print placed between endPacket() and here would blind the radio for
  // roughly three preamble lengths and every single command would be missed —
  // with nothing in either log to show why. Serial and SPI are independent
  // peripherals, so moving the print after costs the window nothing.
  LoRa.receive();

  Serial.printf("%s [%u B] %s\n", sent ? "[TX]" : "[ERROR] TX failed",
                packet.length(), packet.c_str());
}
#endif  // IS_TRANSMITTER

// ═════════════════════════════════════════════════════════════
//  RECEIVER — ground station side
// ═════════════════════════════════════════════════════════════
#ifdef IS_RECEIVER

bool     rx_verbose  = true;   // 'v' toggles to CSV for data capture

bool     have_first  = false;
uint32_t first_pid   = 0;
uint32_t last_pid    = 0;
uint32_t n_good      = 0;      // parsed AND crc valid
uint32_t n_crcfail   = 0;      // arrived, parsed, crc bad
uint32_t n_parsefail = 0;      // arrived, not valid JSON (p_id unknowable)

long     rssi_sum = 0;
float    snr_sum  = 0;
int      rssi_min = 9999, rssi_max = -9999;
float    snr_min  = 9999, snr_max  = -9999;

unsigned long first_rx_ms  = 0;
unsigned long lastPacketMs = 0;
unsigned long lastBeatMs   = 0;

// Pending uplink, set by a '>id,ct,param' line on the serial port. Only one
// command is held at a time — a newer one replaces an unsent older one, which
// is what an operator clicking twice actually means.
bool     up_pending = false;
int32_t  up_id      = 0;
int      up_ct      = 0;
int      up_pm      = 0;
char     up_buf[32] = {0};
uint8_t  up_len     = 0;
bool     up_reading = false;

#ifdef FAULT_TEST
// Fault-injection build only. A '!<text>' serial line is transmitted verbatim
// as one LoRa uplink, bypassing the JSON builder and the CRC, so the
// satellite's rejection paths can be exercised with frames no correct ground
// station would ever emit. Compiled out of the normal receiver build, which
// therefore cannot transmit anything unvalidated.
bool     up_raw_pending = false;
char     up_raw[128]    = {0};
uint8_t  up_raw_len     = 0;
bool     up_raw_reading = false;
#endif

const char* rssi_label(int r) {
  if (r > -60)  return "EXCELLENT";
  if (r > -80)  return "GOOD";
  if (r > -100) return "FAIR";
  if (r > -110) return "WEAK";
  return "MARGINAL";
}

void decode_err(uint8_t e, char* out, size_t n) {
  if (e == 0) { snprintf(out, n, "none"); return; }
  out[0] = '\0';
  if (e & ERR_BMP)      strncat(out, "BMP280 ",  n - strlen(out) - 1);
  if (e & ERR_MPU)      strncat(out, "MPU6050 ", n - strlen(out) - 1);
  if (e & ERR_GPS)      strncat(out, "NO-GPS-FIX ", n - strlen(out) - 1);
  if (e & ERR_OVERSIZE) strncat(out, "OVERSIZE ", n - strlen(out) - 1);
}

void reset_stats() {
  have_first = false;
  first_pid = last_pid = 0;
  n_good = n_crcfail = n_parsefail = 0;
  rssi_sum = 0; snr_sum = 0;
  rssi_min = 9999; rssi_max = -9999;
  snr_min  = 9999; snr_max  = -9999;
  first_rx_ms = 0;
}

// Every line starts with '#' so a captured log still imports as CSV.
void print_summary() {
  if (!have_first || n_good == 0) {
    Serial.println("# no packets yet — nothing to summarise");
    return;
  }
  uint32_t expected  = last_pid - first_pid + 1;
  uint32_t accounted = n_good + n_crcfail;          // had a readable p_id
  uint32_t lost      = expected > accounted ? expected - accounted : 0;
  uint32_t arrived   = n_good + n_crcfail + n_parsefail;
  float pdr    = 100.0f * n_good / expected;
  float usable = arrived ? (100.0f * n_good / arrived) : 0.0f;
  unsigned long el = (millis() - first_rx_ms) / 1000;

  Serial.println("#");
  Serial.println("# +--------------------- LINK SUMMARY ---------------------+");
  Serial.printf ("# | PDR (link)   %6.2f %%   %lu good / %lu sent\n",
                 pdr, (unsigned long)n_good, (unsigned long)expected);
  Serial.printf ("# | Usable       %6.2f %%   %lu good / %lu arrived\n",
                 usable, (unsigned long)n_good, (unsigned long)arrived);
  Serial.printf ("# | Lost (RF)    %6lu     never arrived\n", (unsigned long)lost);
  Serial.printf ("# | CRC fail     %6lu     arrived corrupted\n", (unsigned long)n_crcfail);
  Serial.printf ("# | Parse fail   %6lu     arrived unreadable\n", (unsigned long)n_parsefail);
  Serial.printf ("# | p_id range   %lu .. %lu\n",
                 (unsigned long)first_pid, (unsigned long)last_pid);
  Serial.printf ("# | RSSI   avg %6.1f   min %4d   max %4d  dBm   [%s]\n",
                 (float)rssi_sum / n_good, rssi_min, rssi_max,
                 rssi_label((int)((float)rssi_sum / n_good)));
  Serial.printf ("# | SNR    avg %6.1f   min %4.1f   max %4.1f  dB\n",
                 snr_sum / n_good, snr_min, snr_max);
  Serial.printf ("# | Elapsed    %6lu s   cadence %.2f s/packet\n",
                 el, n_good > 1 ? (float)el / (n_good - 1) : 0.0f);
  Serial.println("# +--------------------------------------------------------+");
  Serial.println("#");
}

// Explicit setter, not just a toggle — the host logger has no way to know the
// current mode, so it needs an idempotent "be in CSV mode" command.
void set_rx_mode(bool verbose) {
  rx_verbose = verbose;
  if (verbose) {
    Serial.println("# --- verbose mode ---");
  } else {
    Serial.println("# --- CSV mode ---");
    // alt_kf is appended last, not slotted next to alt, so every archived
    // range-test log still parses against this header by column name.
    Serial.println("rx_ms,p_id,rssi,snr,crc,ts,mode,temp,press,alt,"
                   "ax,ay,az,gx,gy,gz,roll,pitch,yaw,"
                   "lat,lon,gps_alt,sats,volt,err,ack,alt_kf");
  }
}

void print_help() {
  Serial.println("#");
  Serial.println("# keys:  v = toggle verbose/CSV   c = force CSV    b = force verbose");
  Serial.println("#        r = reset stats          s = summary now  h = this help");
  Serial.println("# uplink: >id,ct,param<enter>");
  Serial.println("#         ct 1=mode (param 2=normal, 1=safe)  2=calibrate  3=beep (param=ms)");
#ifdef FAULT_TEST
  Serial.println("# FAULT_TEST build: !<text><enter> transmits <text> verbatim, no CRC");
#endif
  Serial.println("#");
}

// ── Uplink ──────────────────────────────────────────────────
// The satellite only opens its receive window for 500 ms immediately after it
// transmits, so a command cannot be sent on demand. It is queued here and fired
// the instant a downlink decodes — the one moment the far end is known to be
// listening.
void queue_uplink(int32_t id, int ct, int pm) {
  up_id      = id;
  up_ct      = ct;
  up_pm      = pm;
  up_pending = true;
  Serial.printf("# UPLINK_QUEUED id=%ld ct=%d pm=%d\n", (long)id, ct, pm);
}

void send_uplink() {
  StaticJsonDocument<96> doc;
  doc["id"] = up_id;
  doc["ct"] = up_ct;
  doc["pm"] = up_pm;

  String payload;
  serializeJson(doc, payload);
  char crc_str[5];
  sprintf(crc_str, "%04X",
          compute_crc16((const uint8_t*)payload.c_str(), payload.length()));
  doc["c"] = crc_str;

  String frame;
  serializeJson(doc, frame);

  // ~40 B at SF7/125 kHz is roughly 55 ms of airtime — well inside the
  // satellite's 500 ms window, and long done before its next transmission.
  LoRa.beginPacket();
  LoRa.print(frame);
  bool sent = LoRa.endPacket();

  up_pending = false;
  Serial.printf("# UPLINK_%s id=%ld ct=%d pm=%d | %s\n",
                sent ? "TX" : "FAIL", (long)up_id, up_ct, up_pm, frame.c_str());
}
#ifdef FAULT_TEST

// Transmits up_raw verbatim: no JSON, no CRC, no validation of any kind. Uses
// the same call sequence and the same receive window as send_uplink(), so the
// only thing the satellite can observe differently is the payload itself.
void send_uplink_raw() {
  LoRa.beginPacket();
  LoRa.print(up_raw);
  bool sent = LoRa.endPacket();

  up_raw_pending = false;
  Serial.printf("# RAW_%s %u B | %s\n", sent ? "TX" : "FAIL",
                (unsigned)strlen(up_raw), up_raw);
}
#endif
#endif  // IS_RECEIVER

// ─────────────────────────────────────────────────────────────
// Setup
// ─────────────────────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(2000);

  LoRa.setPins(LORA_NSS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println("[ERROR] LoRa init failed");
    while (1);
  }
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setPreambleLength(LORA_PREAMBLE);
  LoRa.setTxPower(17);

  #ifdef IS_TRANSMITTER
    Wire.begin(SDA_PIN, SCL_PIN);
    Wire.setClock(400000);   // 100 kHz cannot sustain a 50 Hz IMU sample rate

    if (!bmp.begin(BMP280_ADDR)) {
      Serial.println("[WARN] BMP280 not found");
      sensor_err |= ERR_BMP;
    } else {
      bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                      Adafruit_BMP280::SAMPLING_X2,
                      Adafruit_BMP280::SAMPLING_X16,
                      Adafruit_BMP280::FILTER_X4,
                      Adafruit_BMP280::STANDBY_MS_500);
    }

    mpu.initialize();
    if (!mpu.testConnection()) {
      Serial.println("[WARN] MPU6050 not found");
      sensor_err |= ERR_MPU;
    }

    gps_open(GPS_PIN_A, GPS_PIN_B);   // orientation confirmed by gps_probe()

    ledcSetup(0, 2000, 8);
    ledcAttachPin(BUZZER_PIN, 0);
    ledcWriteTone(0, 0);

  #if HAS_VBAT_DIVIDER
    analogReadResolution(12);
    analogSetAttenuation(ADC_11db);
  #endif

    madgwick.begin(IMU_SAMPLE_HZ);

    // The satellite is stationary on the bench at boot — the one moment a gyro
    // bias measurement is trustworthy without an explicit command.
    calibrate_gyro();

    Serial.println("[TTC] Transmitter ready — 433 MHz SF7 125kHz");
    Serial.printf("[TTC] telemetry %d ms | RX window %d ms | IMU %.0f Hz | battery %s\n",
                  TELEMETRY_INTERVAL_MS, RX_WINDOW_MS, IMU_SAMPLE_HZ,
                  HAS_VBAT_DIVIDER ? "ADC" : "placeholder");
  #endif

  #ifdef IS_RECEIVER
    Serial.println("# TTC ground station — 433 MHz SF7 125kHz CR4/5");
    Serial.println("# receiver ready, listening...");
    print_help();
  #endif
}

// ─────────────────────────────────────────────────────────────
// Loop
// ─────────────────────────────────────────────────────────────
void loop() {

// =============================================================
//  TRANSMITTER
// =============================================================
#ifdef IS_TRANSMITTER

  unsigned long now = millis();

  // Highest priority: the GPS UART. Everything below is scheduled around it.
  gps_pump();
  gps_probe();
  gps_health();
  buzzer_service();

  // 50 Hz attitude update. Sampling the IMU only at packet time would leave
  // Madgwick with nothing to integrate and roll/pitch/yaw pinned near zero —
  // which is what the old placeholder fields effectively reported.
  if (now - last_imu_ms >= IMU_PERIOD_MS) {
    last_imu_ms = now;
    if (!(sensor_err & ERR_MPU)) imu_sample();
  }

  // RX window — poll for a command uplink for 500 ms after each downlink.
  // Polled rather than spun in a while loop so the GPS and the IMU keep being
  // serviced for the whole window.
  if (rx_open && now - rx_open_ms >= RX_GUARD_MS) {
    int sz = LoRa.parsePacket();
    if (sz) {
      String cmd = "";
      while (LoRa.available()) cmd += (char)LoRa.read();
      handle_command(cmd);
    }
  }
  if (rx_open && now - rx_open_ms >= RX_WINDOW_MS) {
    LoRa.idle();
    rx_open = false;
  }

  // Downlink every TELEMETRY_INTERVAL_MS.
  if (now - last_tx_ms >= TELEMETRY_INTERVAL_MS) {
    float dt_s = last_tx_ms == 0 ? TELEMETRY_INTERVAL_MS / 1000.0f
                                 : (now - last_tx_ms) / 1000.0f;
    last_tx_ms = now;

    if (rx_open) { LoRa.idle(); rx_open = false; }   // never TX mid-window
    send_telemetry(dt_s);   // leaves the radio already in receive mode
    p_id++;

    rx_open_ms = millis();
    rx_open    = true;
  }

#endif

// =============================================================
//  RECEIVER
// =============================================================
#ifdef IS_RECEIVER

  // ── Keyboard control ──
  // pio device monitor forwards keystrokes to the board, so the display mode
  // and the stats can be driven from the field without a reflash.
  // A line starting with '>' is a command uplink from the ground-station
  // backend. It is kept distinct from the single-key controls so the display
  // keys and the automated link can share one serial port.
  while (Serial.available()) {
    char ch = (char)Serial.read();

#ifdef FAULT_TEST
    // Raw uplink line: everything up to the newline is the payload, so no
    // character is special and a deliberately malformed frame survives intact.
    if (up_raw_reading) {
      if (ch == '\n' || ch == '\r') {
        up_raw[up_raw_len] = '\0';
        up_raw_pending = up_raw_len > 0;
        up_raw_reading = false;
        if (up_raw_pending) {
          Serial.printf("# RAW_QUEUED %u B | %s\n",
                        (unsigned)up_raw_len, up_raw);
        } else {
          Serial.println("# RAW_FAIL empty payload");
        }
        up_raw_len = 0;
      } else if (up_raw_len < sizeof(up_raw) - 1) {
        up_raw[up_raw_len++] = ch;
      }
      continue;
    }
#endif

    if (up_reading) {
      if (ch == '\n' || ch == '\r') {
        up_buf[up_len] = '\0';
        long id = 0; int ct = 0, pm = 0;
        if (sscanf(up_buf, "%ld,%d,%d", &id, &ct, &pm) == 3) {
          queue_uplink((int32_t)id, ct, pm);
        } else {
          Serial.printf("# UPLINK_FAIL bad syntax: >%s\n", up_buf);
        }
        up_reading = false;
        up_len     = 0;
      } else if (up_len < sizeof(up_buf) - 1) {
        up_buf[up_len++] = ch;
      }
      continue;
    }

    switch (ch) {
      case '>': up_reading = true; up_len = 0;      break;
#ifdef FAULT_TEST
      case '!': up_raw_reading = true; up_raw_len = 0; break;
#endif
      case 'v': case 'V': set_rx_mode(!rx_verbose); break;
      case 'c': case 'C': set_rx_mode(false);       break;
      case 'b': case 'B': set_rx_mode(true);        break;
      case 'r': case 'R':
        print_summary();
        reset_stats();
        Serial.println("# === stats reset — new measurement point ===");
        break;
      case 's': case 'S': print_summary(); break;
      case 'h': case 'H': print_help();    break;
    }
  }

  int packetSize = LoRa.parsePacket();
  if (packetSize == 0) {
    // Heartbeat as a '#' comment so it survives CSV import. Without it, a dead
    // receiver and a receiver out of range look identical from the laptop.
    // Only fires once the link has actually gone quiet — at a healthy ~2.4 s
    // cadence this stays silent instead of interleaving with the CSV rows.
    if (millis() - lastPacketMs > 8000 && millis() - lastBeatMs > 5000) {
      lastBeatMs = millis();
      Serial.printf("# listening... %lus since last packet\n",
                    (unsigned long)((millis() - lastPacketMs) / 1000));
    }
    return;
  }
  lastPacketMs = millis();

  // Reserve up front: String::concat reallocates on every single character, so
  // draining a 240-byte packet costs 240 mallocs and memcpys. That delay sits
  // directly in front of send_uplink() below, inside the window the satellite
  // is listening in, which is the one place in this loop where milliseconds
  // are worth buying.
  String raw = "";
  raw.reserve(packetSize + 1);
  while (LoRa.available()) {
    raw += (char)LoRa.read();
  }

  int   rssi = LoRa.packetRssi();
  float snr  = LoRa.packetSnr();

  // Fire any queued command right here: the satellite opened its 500 ms
  // receive window the instant it finished sending this packet, and that is
  // the only moment it is listening. Sent before parsing so the JSON work
  // does not eat into the window. The FIFO has already been drained above,
  // so transmitting cannot clobber the packet we just read.
#ifdef FAULT_TEST
  // Fault frames go first and are mutually exclusive with a normal uplink in
  // practice, since fault_test.py never queues both in one window.
  if (up_raw_pending) send_uplink_raw();
#endif
  if (up_pending) send_uplink();

  // Parse JSON
  StaticJsonDocument<512> doc;
  DeserializationError err = deserializeJson(doc, raw);

  if (err) {
    n_parsefail++;
    Serial.printf("# PARSE_FAIL %s | %u B | rssi=%d snr=%.1f | %s\n",
                  err.c_str(), raw.length(), rssi, snr, raw.c_str());
    return;
  }

  // Validasi CRC — strip "c" and re-serialize to reproduce the exact string
  // the transmitter hashed. Depends on ArduinoJson preserving key order, which
  // it does, and on floats round-tripping identically through parse/serialize.
  String crc_received = doc["c"] | "0000";
  doc.remove("c");
  String payload_check;
  serializeJson(doc, payload_check);
  uint16_t crc_calc = compute_crc16((const uint8_t*)payload_check.c_str(),
                                    payload_check.length());
  char crc_calc_str[5];
  sprintf(crc_calc_str, "%04X", crc_calc);

  bool crc_ok = (crc_received == String(crc_calc_str));

  uint32_t pid = doc["i"] | 0;

  // Transmitter reboot restarts p_id — start a fresh run instead of logging a
  // nonsense PDR for the rest of the session.
  if (have_first && pid < last_pid) {
    Serial.println("# p_id went backwards — transmitter restarted, stats reset");
    reset_stats();
  }
  if (!have_first) {
    have_first  = true;
    first_pid   = pid;
    first_rx_ms = millis();
  } else if (pid > last_pid + 1) {
    // Flag RF loss the moment it happens — waiting for the summary hides
    // exactly where in the walk the link started dropping.
    Serial.printf("# [!] GAP  p_id %lu..%lu missing (%lu lost)\n",
                  (unsigned long)(last_pid + 1), (unsigned long)(pid - 1),
                  (unsigned long)(pid - last_pid - 1));
  }
  last_pid = pid;

  if (crc_ok) {
    n_good++;
    rssi_sum += rssi;
    snr_sum  += snr;
    if (rssi < rssi_min) rssi_min = rssi;
    if (rssi > rssi_max) rssi_max = rssi;
    if (snr  < snr_min)  snr_min  = snr;
    if (snr  > snr_max)  snr_max  = snr;
  } else {
    n_crcfail++;
  }

  int   sats = doc["s"] | 0;
  int   mode = doc["m"] | 0;
  uint8_t e  = (uint8_t)(doc["e"] | 0);
  int32_t ack = doc["a"] | 0;

  if (rx_verbose) {
    uint32_t expected = last_pid - first_pid + 1;
    float pdr = expected > 0 ? (100.0f * n_good / expected) : 0.0f;
    char errbuf[48];
    decode_err(e, errbuf, sizeof(errbuf));

    Serial.printf("\n[%5lu] RSSI %4d dBm (%s)   SNR %5.1f dB   CRC %s   PDR %.1f%% (%lu/%lu)\n",
      (unsigned long)pid, rssi, rssi_label(rssi), snr,
      crc_ok ? "OK" : "** FAIL **", pdr,
      (unsigned long)n_good, (unsigned long)expected);
    Serial.printf("        env     %6.1f C  %6ld Pa   alt %7.1f m (kf %7.1f)  |  ts %lu   mode %s\n",
      (float)(doc["tp"] | 0.0f), (long)(doc["pr"] | 0L), (float)(doc["al"] | 0.0f),
      (float)(doc["af"] | 0.0f), (unsigned long)(doc["t"] | 0),
      mode == 2 ? "NORMAL" : (mode == 1 ? "SAFE" : "?"));
    Serial.printf("        accel   %6.2f /%6.2f /%6.2f g       |  gyro %6.1f /%6.1f /%6.1f deg/s\n",
      (float)(doc["ax"] | 0.0f), (float)(doc["ay"] | 0.0f), (float)(doc["az"] | 0.0f),
      (float)(doc["gx"] | 0.0f), (float)(doc["gy"] | 0.0f), (float)(doc["gz"] | 0.0f));
    Serial.printf("        orient  r %5.1f  p %5.1f  y %5.1f deg  |  batt %.2f V   ack %s%ld\n",
      (float)(doc["r"] | 0.0f), (float)(doc["p"] | 0.0f), (float)(doc["y"] | 0.0f),
      (float)(doc["v"] | 0.0f),
      ack == ACK_NAK ? "NAK " : (ack == 0 ? "none " : "cmd_id "), (long)ack);
    Serial.printf("        GPS     %s (%d sat)  %.6f, %.6f  alt %.1f m   |  err %s\n",
      sats > 0 ? "FIX   " : "NO FIX", sats,
      (double)(doc["la"] | 0.0), (double)(doc["lo"] | 0.0),
      (float)(doc["ga"] | 0.0f), errbuf);
  } else {
    // ── CSV row ──
    Serial.printf("%lu,%lu,%d,%.1f,%s,%lu,%d,",
      (unsigned long)millis(), (unsigned long)pid, rssi, snr,
      crc_ok ? "OK" : "FAIL",
      (unsigned long)(doc["t"] | 0), mode);
    Serial.printf("%.1f,%ld,%.1f,",
      (float)(doc["tp"] | 0.0f), (long)(doc["pr"] | 0L), (float)(doc["al"] | 0.0f));
    Serial.printf("%.2f,%.2f,%.2f,%.1f,%.1f,%.1f,",
      (float)(doc["ax"] | 0.0f), (float)(doc["ay"] | 0.0f), (float)(doc["az"] | 0.0f),
      (float)(doc["gx"] | 0.0f), (float)(doc["gy"] | 0.0f), (float)(doc["gz"] | 0.0f));
    Serial.printf("%.1f,%.1f,%.1f,",
      (float)(doc["r"] | 0.0f), (float)(doc["p"] | 0.0f), (float)(doc["y"] | 0.0f));
    Serial.printf("%.6f,%.6f,%.1f,%d,%.2f,%d,%ld,%.1f\n",
      (double)(doc["la"] | 0.0), (double)(doc["lo"] | 0.0),
      (float)(doc["ga"] | 0.0f), sats,
      (float)(doc["v"] | 0.0f), e, (long)ack, (float)(doc["af"] | 0.0f));
  }

  // ── Rolling link summary every 20 good packets ──
  if (n_good > 0 && n_good % 20 == 0) {
    print_summary();
  }

#endif
}
