import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip, Legend } from 'recharts';

const ERR_GPS = 0x04;

function TelemetryChart({ history }) {
  // Altitude and temperature differ by two orders of magnitude (~160 m vs
  // ~40 C). Sharing one Y axis stretched it from 40 to 185, which squeezed the
  // raw and fused altitude traces — typically 5 m apart — into the same few
  // pixels and made the Kalman filter look like it was doing nothing. They get
  // their own axes now: altitude left, temperature right.
  const data = history.map((point, idx) => ({
    idx,
    temperature: point.temperature,
    altitude: point.altitude,
    alt_kf: point.alt_kf,
    // The satellite sends gps_alt = 0 when it has no fix, which would drag the
    // altitude axis down to zero and flatten everything above it. Gate it on
    // the same fix test the firmware uses and let Recharts break the line.
    gps_alt:
      point.sats > 0 && !(point.err & ERR_GPS) ? point.gps_alt : null,
  }));

  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '12px',
        padding: '18px',
      }}
    >
      <p
        style={{
          fontSize: '11px',
          color: 'var(--text-secondary)',
          letterSpacing: '0.5px',
          marginBottom: '16px',
        }}
      >
        ALTITUDE SENSOR FUSION &amp; TEMPERATURE — LIVE
      </p>

      <div style={{ width: '100%', height: '260px' }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 4, left: -8 }}>
            <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" />
            <XAxis dataKey="idx" stroke="var(--text-secondary)" fontSize={11} tickLine={false} />
            <YAxis
              yAxisId="alt"
              domain={['auto', 'auto']}
              stroke="var(--text-secondary)"
              fontSize={11}
              tickLine={false}
              width={48}
            />
            <YAxis
              yAxisId="temp"
              orientation="right"
              domain={['auto', 'auto']}
              stroke="var(--text-secondary)"
              fontSize={11}
              tickLine={false}
              width={40}
            />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-card-hover)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '8px',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
              }}
            />
            <Legend
              wrapperStyle={{ fontSize: '11px', fontFamily: 'var(--font-mono)' }}
              iconType="plainline"
              iconSize={18}
            />

            {/* The two filter inputs are drawn thin and broken; the fused
                output is the only solid altitude trace. The filter is working
                when the solid line sits between the two dashed ones and much
                nearer the barometer, which is the 0.8 / 0.2 weighting in
                main.cpp:457 made visible. */}
            <Line
              yAxisId="alt"
              type="monotone"
              dataKey="gps_alt"
              stroke="#4DA3FF"
              strokeWidth={1}
              strokeDasharray="2 3"
              dot={false}
              connectNulls={false}
              name="GPS alt (m)"
            />
            <Line
              yAxisId="alt"
              type="monotone"
              dataKey="altitude"
              stroke="#FFB020"
              strokeWidth={1}
              strokeDasharray="4 3"
              dot={false}
              name="Baro alt (m)"
            />
            <Line
              yAxisId="alt"
              type="monotone"
              dataKey="alt_kf"
              stroke="#FFB020"
              strokeWidth={2.5}
              dot={false}
              name="Kalman alt (m)"
            />
            <Line
              yAxisId="temp"
              type="monotone"
              dataKey="temperature"
              stroke="#00FF9C"
              strokeWidth={2}
              dot={false}
              name="Temp (C)"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default TelemetryChart;
