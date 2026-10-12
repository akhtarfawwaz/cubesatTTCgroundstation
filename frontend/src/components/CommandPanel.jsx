// cmd_type / value are the ICD encoding the satellite firmware acts on
// (thesis Table 4.8): ct 1 = set mode, where the param matches the mode field
// itself (2 = Normal, 1 = Safe); ct 3 = beep, param is the duration in ms.
const commands = [
  { id: 'beep', label: 'Buzzer', payload: { cmd_type: 3, value: 300 } },
  { id: 'normal', label: 'Normal Mode', payload: { cmd_type: 1, value: 2 } },
  { id: 'safe', label: 'Safe Mode', payload: { cmd_type: 1, value: 1 } },
  { id: 'calibrate', label: 'Sensor Calibration', payload: { cmd_type: 2, value: 0 } },
];

function CommandPanel({ onSend, pending, disabled }) {
  // A command is only "done" once the satellite echoes it back on a later
  // downlink, so the button stays lit for the whole round trip rather than
  // flashing on click and implying a delivery that has not happened yet.
  const isPending = (cmd) =>
    pending &&
    pending.cmd_type === cmd.payload.cmd_type &&
    pending.value === cmd.payload.value;

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
          marginBottom: '14px',
        }}
      >
        COMMAND UPLINK
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
        {commands.map((cmd) => {
          const active = isPending(cmd);
          return (
            <button
              key={cmd.id}
              onClick={() => onSend(cmd.payload)}
              disabled={disabled || !!pending}
              style={{
                background: active ? 'var(--accent-amber)' : 'var(--bg-card-hover)',
                color: active ? '#0A0A0B' : 'var(--text-primary)',
                border: '1px solid var(--border-subtle)',
                borderRadius: '8px',
                padding: '12px 8px',
                fontSize: '13px',
                fontFamily: 'var(--font-sans)',
                cursor: disabled || pending ? 'not-allowed' : 'pointer',
                opacity: disabled || (pending && !active) ? 0.45 : 1,
                transition: 'all 0.2s ease',
              }}
            >
              {active ? 'SENT — WAITING' : cmd.label}
            </button>
          );
        })}
      </div>

      <p
        style={{
          fontSize: '11px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-secondary)',
          marginTop: '12px',
        }}
      >
        {disabled
          ? 'Uplink unavailable — receiver serial port closed'
          : 'Transmitted in the satellite’s 500 ms receive window'}
      </p>
    </div>
  );
}

export default CommandPanel;