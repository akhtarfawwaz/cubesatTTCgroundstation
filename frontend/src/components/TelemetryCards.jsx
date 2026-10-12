function TelemetryCards({ telemetry }) {
  // ALTITUDE is the Kalman-fused estimate, not the raw barometric reading —
  // that is the value the OBC actually flies on. The raw one is kept beside it
  // so a filter that has diverged is visible rather than hidden.
  const topCards = [
    { label: 'TEMPERATURE', value: telemetry?.temperature, unit: '°C' },
    { label: 'PRESSURE', value: telemetry?.pressure, unit: 'Pa' },
    {
      label: 'ALTITUDE',
      value: telemetry?.alt_kf,
      unit: 'm',
      note: telemetry?.altitude !== undefined ? `raw ${telemetry.altitude} m` : null,
    },
  ];

  const bottomCards = [
    // The EPS is outside this thesis's scope — the prototype runs off USB or a
    // power bank and no divider is fitted, so the firmware transmits a fixed
    // 3.70 (read_battery(), main.cpp). The field is kept because it is part of
    // the ICD and proves the telemetry path carries it, but it is rendered
    // muted and labelled so nobody reads a constant as a measurement.
    {
      label: 'BUS VOLTAGE',
      value: telemetry?.volt,
      unit: 'V',
      muted: true,
      note: 'placeholder — externally powered, not measured',
    },
    {
      label: 'MODE',
      value: telemetry?.mode !== undefined
        ? telemetry.mode === 1 ? 'SAFE' : 'NORMAL'
        : undefined,
      unit: '',
    },
    { label: 'ERROR CODE', value: telemetry?.err, unit: '' },
  ];

  const renderCard = (card) => (
    <div
      key={card.label}
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
          marginBottom: '8px',
        }}
      >
        {card.label}
      </p>
      <p
        style={{
          fontFamily: 'var(--font-mono)',
          fontSize: '26px',
          fontWeight: 500,
          color: card.muted
            ? 'var(--text-secondary)'
            : card.label === 'ERROR CODE' && card.value !== 0
            ? 'var(--accent-amber)'
            : card.label === 'MODE' && telemetry?.mode === 1
            ? 'var(--accent-amber)'
            : 'var(--accent-green)',
        }}
      >
        {card.value !== undefined ? card.value : '--'}
        <span style={{ fontSize: '14px', color: 'var(--text-secondary)', marginLeft: '4px' }}>
          {card.unit}
        </span>
      </p>
      {card.note && (
        <p
          style={{
            fontSize: '11px',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-secondary)',
            marginTop: '4px',
          }}
        >
          {card.note}
        </p>
      )}
    </div>
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {topCards.map(renderCard)}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {bottomCards.map(renderCard)}
      </div>
    </div>
  );
}

export default TelemetryCards;