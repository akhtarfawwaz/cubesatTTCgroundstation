const ERR_GPS = 0x04;   // err bitfield, ICD Section 4 / Receiver/src/main.cpp

function GPSPanel({ telemetry }) {
  // The satellite ALWAYS transmits coordinates: with no fix it substitutes the
  // hardcoded fallback -7.7956, 110.3695 (main.cpp:439). So the presence of
  // lat/lon proves nothing — fix state has to come from the satellite count,
  // the same gate the firmware itself uses (main.cpp:435), cross-checked
  // against the ERR_GPS flag it raises alongside it.
  const hasTelemetry = telemetry !== null && telemetry !== undefined;
  const fix = hasTelemetry && telemetry.sats > 0 && !(telemetry.err & ERR_GPS);

  const status = fix
    ? { text: 'FIX', bg: 'rgba(0,255,156,0.1)', fg: 'var(--accent-green)' }
    : { text: 'NO FIX', bg: 'rgba(255,68,68,0.1)', fg: 'var(--accent-red)' };

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
            background: status.bg,
            color: status.fg,
          }}
        >
          {status.text}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>LATITUDE</p>
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '16px',
              color: fix ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            {fix ? telemetry.latitude.toFixed(6) : '--'}
          </p>
        </div>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>LONGITUDE</p>
          <p
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '16px',
              color: fix ? 'var(--text-primary)' : 'var(--text-secondary)',
            }}
          >
            {fix ? telemetry.longitude.toFixed(6) : '--'}
          </p>
        </div>

        {hasTelemetry && !fix && (
          <p style={{ fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--accent-amber)' }}>
            fallback position — not a GPS fix
          </p>
        )}

        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>GPS ALTITUDE</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {fix ? `${telemetry.gps_alt.toFixed(1)} m` : '--'}
          </p>
        </div>
        <div>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>SATELLITES</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '16px' }}>
            {hasTelemetry ? telemetry.sats : '--'}
          </p>
        </div>
      </div>
    </div>
  );
}

export default GPSPanel;
