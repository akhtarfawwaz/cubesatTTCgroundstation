function TelemetryCards({ telemetry }) {
  const topCards = [
    { label: 'TEMPERATURE', value: telemetry?.temperature, unit: '°C' },
    { label: 'PRESSURE', value: telemetry?.pressure, unit: 'Pa' },
    { label: 'ALTITUDE', value: telemetry?.altitude, unit: 'm' },
  ];

  const bottomCards = [
    { label: 'VOLTAGE', value: telemetry?.volt, unit: 'V' },
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
          color: card.label === 'ERROR CODE' && card.value !== 0
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