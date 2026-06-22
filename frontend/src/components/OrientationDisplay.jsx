function OrientationDisplay({ telemetry }) {
  const axes = [
    { label: 'ROLL', value: telemetry?.roll },
    { label: 'PITCH', value: telemetry?.pitch },
    { label: 'YAW', value: telemetry?.yaw },
  ];

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
        ORIENTATION
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '16px' }}>
        {axes.map((axis) => (
          <div key={axis.label} style={{ textAlign: 'center' }}>
            <div
              style={{
                width: '64px',
                height: '64px',
                margin: '0 auto 10px',
                borderRadius: '50%',
                border: '2px solid var(--border-subtle)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                position: 'relative',
              }}
            >
              <div
                style={{
                  width: '2px',
                  height: '26px',
                  background: 'var(--accent-green)',
                  position: 'absolute',
                  top: '6px',
                  transform: `rotate(${axis.value || 0}deg)`,
                  transformOrigin: 'bottom center',
                  transition: 'transform 0.3s ease',
                }}
              />
            </div>
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{axis.label}</p>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: '15px', marginTop: '2px' }}>
              {axis.value !== undefined ? `${axis.value}°` : '--'}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

export default OrientationDisplay;