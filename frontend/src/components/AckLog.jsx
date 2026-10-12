const CMD_NAMES = {
  1: 'MODE',
  2: 'CALIB',
  3: 'BUZZER',
};

// 'executed' and 'nak' both come from the satellite itself, in field "a" of a
// later downlink: the cmd_id echoed back means ACK, -1 means it heard the
// command and rejected it. Everything else is the ground station reporting that
// no such echo arrived at all.
const STATUS = {
  executed:   { text: 'ACK',        color: 'var(--accent-green)' },
  nak:        { text: 'NAK',        color: 'var(--accent-red)' },
  timeout:    { text: 'TIMEOUT',    color: 'var(--accent-amber)' },
  superseded: { text: 'SUPERSEDED', color: 'var(--text-secondary)' },
  failed:     { text: 'FAILED',     color: 'var(--accent-red)' },
  rejected:   { text: 'REJECTED',   color: 'var(--accent-red)' },
};

function describe(log) {
  const name = CMD_NAMES[log.cmd_type] || `CT${log.cmd_type ?? '?'}`;
  const id = log.cmd_id !== undefined ? `#${log.cmd_id} ` : '';
  if (log.cmd_type === 1) return `${id}${name} ${log.value === 2 ? 'NORMAL' : 'SAFE'}`;
  if (log.cmd_type === 3) return `${id}${name} ${log.value} ms`;
  return `${id}${name}`;
}

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

        {logs.map((log, idx) => {
          const status = STATUS[log.status] || { text: log.status, color: 'var(--accent-red)' };
          return (
            <div
              key={idx}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                gap: '8px',
                fontSize: '12px',
                fontFamily: 'var(--font-mono)',
                borderBottom: '1px solid var(--border-subtle)',
                paddingBottom: '6px',
              }}
            >
              <span style={{ color: 'var(--text-secondary)' }}>{log.time}</span>
              <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {describe(log)}
              </span>
              <span
                style={{ color: status.color, fontWeight: 500 }}
                title={log.detail || (log.p_id ? `confirmed on packet #${log.p_id}` : undefined)}
              >
                {status.text}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default AckLog;