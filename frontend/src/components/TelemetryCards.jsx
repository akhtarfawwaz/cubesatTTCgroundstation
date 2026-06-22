function TelemetryCards({ telemetry }) {
  const cards = [
    { label: 'TEMPERATURE', value: telemetry?.temperature, unit: '°C' },
    { label: 'PRESSURE', value: telemetry?.pressure, unit: 'Pa' },
    { label: 'ALTITUDE', value: telemetry?.altitude, unit: 'm' },
  ];

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '16px',
      }}
    >
      {cards.map((card) => (
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
              color: 'var(--accent-green)',
            }}
          >
            {card.value !== undefined ? card.value : '--'}
            <span
              style={{
                fontSize: '14px',
                color: 'var(--text-secondary)',
                marginLeft: '4px',
              }}
            >
              {card.unit}
            </span>
          </p>
        </div>
      ))}
    </div>
  );
}

export default TelemetryCards;