function Header({ connected, packetId }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        padding: '16px 24px',
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '12px',
      }}
    >
      <div>
        <h1
          style={{
            fontSize: '18px',
            fontWeight: 600,
            letterSpacing: '0.5px',
          }}
        >
          CubeSat TTC Ground Station
        </h1>
        <p
          style={{
            fontSize: '13px',
            color: 'var(--text-secondary)',
            fontFamily: 'var(--font-mono)',
            marginTop: '2px',
          }}
        >
          433 MHz · LoRa Telemetry Link
        </p>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
        {packetId && (
          <div style={{ textAlign: 'right' }}>
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>PACKET ID</p>
            <p style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>#{packetId}</p>
          </div>
        )}

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: connected ? 'var(--accent-green)' : 'var(--accent-red)',
              boxShadow: connected ? '0 0 8px var(--accent-green)' : 'none',
              animation: connected ? 'pulse 2s infinite' : 'none',
            }}
          />
          <span
            style={{
              fontSize: '13px',
              fontFamily: 'var(--font-mono)',
              color: connected ? 'var(--accent-green)' : 'var(--accent-red)',
            }}
          >
            {connected ? 'CONNECTED' : 'DISCONNECTED'}
          </span>
        </div>
      </div>

      <style>{`
        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.4; }
        }
      `}</style>
    </div>
  );
}

export default Header;