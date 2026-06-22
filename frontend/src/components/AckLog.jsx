function AckLog({ logs }) {
  return (
    <div
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border-subtle)',
        borderRadius: '12px',
        padding: '18px',
        maxHeight: '260px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <p
        style={{
          fontSize: '11px',
          color: 'var(--text-secondary)',
          letterSpacing: '0.5px',
          marginBottom: '14px',
        }}
      >
        ACK / NAK LOG
      </p>

      <div style={{ overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {logs.length === 0 && (
          <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
            No commands sent yet.
          </p>
        )}

        {logs.map((log, idx) => (
          <div
            key={idx}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              borderBottom: '1px solid var(--border-subtle)',
              paddingBottom: '6px',
            }}
          >
            <span style={{ color: 'var(--text-secondary)' }}>{log.time}</span>
            <span
              style={{
                color: log.status === 'received' ? 'var(--accent-green)' : 'var(--accent-red)',
                fontWeight: 500,
              }}
            >
              {log.status === 'received' ? 'ACK' : 'NAK'}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default AckLog;