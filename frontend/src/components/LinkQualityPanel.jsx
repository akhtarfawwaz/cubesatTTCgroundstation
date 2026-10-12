import { LineChart, Line, YAxis, ResponsiveContainer, Tooltip } from 'recharts';

// Same thresholds as rssi_label() in Receiver/src/main.cpp and link_stats.py,
// so the dashboard, the backend and the receiver's own verbose output never
// disagree about how good the link is.
function rssiLabel(rssi) {
  if (rssi === null || rssi === undefined) return { text: '--', color: 'var(--text-secondary)' };
  if (rssi > -60) return { text: 'EXCELLENT', color: 'var(--accent-green)' };
  if (rssi > -80) return { text: 'GOOD', color: 'var(--accent-green)' };
  if (rssi > -100) return { text: 'FAIR', color: 'var(--accent-amber)' };
  if (rssi > -110) return { text: 'WEAK', color: 'var(--accent-amber)' };
  return { text: 'MARGINAL', color: 'var(--accent-red)' };
}

function pdrColor(pdr) {
  if (pdr === null || pdr === undefined) return 'var(--text-secondary)';
  if (pdr >= 95) return 'var(--accent-green)';   // the link budget requirement
  if (pdr >= 80) return 'var(--accent-amber)';
  return 'var(--accent-red)';
}

const eventColor = {
  gap: 'var(--accent-amber)',
  parse_fail: 'var(--accent-red)',
  reset: 'var(--accent-amber)',
  uplink: 'var(--accent-green)',
  heartbeat: 'var(--text-secondary)',
};

function Metric({ label, value, unit, color }) {
  return (
    <div>
      <p style={{ fontSize: '10px', color: 'var(--text-secondary)', letterSpacing: '0.5px' }}>
        {label}
      </p>
      <p
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '15px',
          color: color || 'var(--text-primary)',
          marginTop: '2px',
        }}
      >
        {value === null || value === undefined ? '--' : value}
        {unit && value !== null && value !== undefined && (
          <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}> {unit}</span>
        )}
      </p>
    </div>
  );
}

function LinkQualityPanel({ telemetry, stats, history, events, live }) {
  const rssi = telemetry?.rssi;
  const snr = telemetry?.snr;
  const label = rssiLabel(rssi);

  const chartData = history.map((point, idx) => ({
    idx,
    rssi: point.rssi,
    snr: point.snr,
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
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'baseline',
          marginBottom: '14px',
        }}
      >
        <p style={{ fontSize: '11px', color: 'var(--text-secondary)', letterSpacing: '0.5px' }}>
          RF LINK QUALITY
        </p>
        <p
          style={{
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            color: live ? label.color : 'var(--text-secondary)',
          }}
        >
          {live ? label.text : 'STALE'}
        </p>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
        <Metric label="RSSI" value={rssi} unit="dBm" color={live ? label.color : undefined} />
        <Metric label="SNR" value={snr?.toFixed(1)} unit="dB" />
        <Metric
          label="PDR"
          value={stats?.pdr?.toFixed(2)}
          unit="%"
          color={pdrColor(stats?.pdr)}
        />
        <Metric label="CADENCE" value={stats?.cadence?.toFixed(2)} unit="s" />
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 1fr 1fr',
          gap: '12px',
          marginTop: '14px',
          paddingTop: '14px',
          borderTop: '1px solid var(--border-subtle)',
        }}
      >
        <Metric label="GOOD" value={stats?.good} color="var(--accent-green)" />
        <Metric
          label="LOST"
          value={stats?.lost}
          color={stats?.lost ? 'var(--accent-amber)' : undefined}
        />
        <Metric
          label="CRC FAIL"
          value={stats?.crc_fail}
          color={stats?.crc_fail ? 'var(--accent-red)' : undefined}
        />
      </div>

      {stats?.first_pid !== null && stats?.first_pid !== undefined && (
        <p
          style={{
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-secondary)',
            marginTop: '12px',
          }}
        >
          p_id {stats.first_pid}..{stats.last_pid} · avg {stats.rssi_avg ?? '--'} dBm /{' '}
          {stats.snr_avg ?? '--'} dB
        </p>
      )}

      {chartData.length > 1 && (
        <div style={{ width: '100%', height: '90px', marginTop: '12px' }}>
          <ResponsiveContainer>
            <LineChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              {/* Two axes: RSSI sits near -100 dBm and SNR near 0 dB, so a
                  shared scale would flatten the SNR trace into a straight line. */}
              <YAxis yAxisId="rssi" hide domain={['dataMin - 5', 'dataMax + 5']} />
              <YAxis yAxisId="snr" hide domain={['dataMin - 2', 'dataMax + 2']} />
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
                yAxisId="rssi"
                type="monotone"
                dataKey="rssi"
                stroke="#00FF9C"
                strokeWidth={2}
                dot={false}
                name="RSSI (dBm)"
              />
              <Line
                yAxisId="snr"
                type="monotone"
                dataKey="snr"
                stroke="#FFB020"
                strokeWidth={2}
                dot={false}
                name="SNR (dB)"
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {events.length > 0 && (
        <div
          style={{
            marginTop: '14px',
            paddingTop: '12px',
            borderTop: '1px solid var(--border-subtle)',
            maxHeight: '96px',
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: '4px',
          }}
        >
          {events.map((event, idx) => (
            <p
              key={idx}
              style={{
                fontSize: '11px',
                fontFamily: 'var(--font-mono)',
                color: eventColor[event.kind] || 'var(--text-secondary)',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              title={event.text}
            >
              <span style={{ color: 'var(--text-secondary)' }}>{event.time} </span>
              {event.text}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}

export default LinkQualityPanel;
