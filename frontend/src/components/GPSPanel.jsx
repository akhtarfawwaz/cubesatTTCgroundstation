function GPSPanel({ telemetry }) {
  const lat = telemetry?.latitude;
  const lon = telemetry?.longitude;
  const alt = telemetry?.gps_alt;
  const sats = telemetry?.sats;
  const hasFix = lat !== undefined && lon !== undefined;

  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '12px',
        padding: '18px',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
        <p style={{ fontSize: '11px', color: 'var(--text-secondary)', letterSpacing: '0.5px' }}>
          GPS POSITION
        </p>
        <span
          style={{
            fontSize: '10px',
            fontFamily: 'var(--font-mono)',
            padding: '2px 8px',
            borderRadius: '4px',
            background: hasFix ? 'rgba(0,255,156,0.1)' : 'rgba(255,68,68,0.1)',
            color: hasFix ? 'var(--accent-green)' : 'var(--accent-red)',
          }}
        >
          {hasFix ? 'FIX' : 'NO FIX'}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>LATITUDE</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {hasFix ? lat.toFixed(6) : '--'}
          </p>
        </div>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>LONGITUDE</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {hasFix ? lon.toFixed(6) : '--'}
          </p>
        </div>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>GPS ALTITUDE</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {alt !== undefined ? `${alt} m` : '--'}
          </p>
        </div>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>SATELLITES</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {sats !== undefined ? sats : '--'}
          </p>
        </div>
      </div>
    </div>
  );
}

export default GPSPanel;