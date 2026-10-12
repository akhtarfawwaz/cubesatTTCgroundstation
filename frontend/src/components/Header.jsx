// A socket that is up says nothing about whether the satellite is still
// transmitting, so the indicator distinguishes all three ways the chain can
// break: the backend, the USB port, and the RF link itself.
const LINK_STATES = {
  live:        { text: 'LIVE',          color: 'var(--accent-green)',  pulse: true },
  no_signal:   { text: 'NO SIGNAL',     color: 'var(--accent-amber)',  pulse: false },
  serial_down: { text: 'SERIAL DOWN',   color: 'var(--accent-red)',    pulse: false },
  offline:     { text: 'BACKEND OFFLINE', color: 'var(--accent-red)',  pulse: false },
};

function Header({ linkState, serialStatus, packetId, secondsSincePacket }) {
  const state = LINK_STATES[linkState] || LINK_STATES.offline;

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

        <div style={{ textAlign: 'right' }}>
          <p style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>LAST PACKET</p>
          <p style={{ fontFamily: 'var(--font-mono)', fontSize: '14px' }}>
            {secondsSincePacket === null ? '--' : `${secondsSincePacket.toFixed(0)}s ago`}
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: state.color,
              boxShadow: state.pulse ? `0 0 8px ${state.color}` : 'none',
              animation: state.pulse ? 'pulse 2s infinite' : 'none',
            }}
          />
          <span
            style={{
              fontSize: '13px',
              fontFamily: 'var(--font-mono)',
              color: state.color,
            }}
            title={linkState === 'serial_down' ? serialStatus?.detail : undefined}
          >
            {state.text}
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