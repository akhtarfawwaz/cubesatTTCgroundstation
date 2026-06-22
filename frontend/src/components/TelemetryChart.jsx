import { LineChart, Line, XAxis, YAxis, CartesianGrid, ResponsiveContainer, Tooltip } from 'recharts';

function TelemetryChart({ history }) {
  const data = history.map((point, idx) => ({
    idx,
    temperature: point.temperature,
    altitude: point.altitude,
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
        TEMPERATURE &amp; ALTITUDE — LIVE
      </p>

      <div style={{ width: '100%', height: '220px' }}>
        <ResponsiveContainer>
          <LineChart data={data}>
            <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="3 3" />
            <XAxis dataKey="idx" stroke="var(--text-secondary)" fontSize={11} tickLine={false} />
            <YAxis stroke="var(--text-secondary)" fontSize={11} tickLine={false} />
            <Tooltip
              contentStyle={{
                background: 'var(--bg-card-hover)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '8px',
                fontFamily: 'var(--font-mono)',
                fontSize: '12px',
              }}
            />
            <Line
              type="monotone"
              dataKey="temperature"
              stroke="#00FF9C"
              strokeWidth={2}
              dot={false}
              name="Temp (°C)"
            />
            <Line
              type="monotone"
              dataKey="altitude"
              stroke="#FFB020"
              strokeWidth={2}
              dot={false}
              name="Altitude (m)"
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

export default TelemetryChart;