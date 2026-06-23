import { useState } from 'react';

function CommandPanel({ onSend }) {
  const [selected, setSelected] = useState(null);

  const commands = [
    { id: 'beep', label: 'Buzzer', payload: { cmd_type: 3 } },
    { id: 'normal', label: 'Normal Mode', payload: { cmd_type: 1, value: 1 } },
    { id: 'safe', label: 'Safe Mode', payload: { cmd_type: 1, value: 0 } },
    { id: 'calibrate', label: 'Sensor Calibration', payload: { cmd_type: 2 } },
  ];

  const handleSend = (cmd) => {
    setSelected(cmd.id);
    onSend(cmd.payload);
    setTimeout(() => setSelected(null), 400);
  };

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
        {commands.map((cmd) => (
          <button
            key={cmd.id}
            onClick={() => handleSend(cmd)}
            style={{
              background: selected === cmd.id ? 'var(--accent-green)' : 'var(--bg-card-hover)',
              color: selected === cmd.id ? '#0A0A0B' : 'var(--text-primary)',
              border: '1px solid var(--border-subtle)',
              borderRadius: '8px',
              padding: '12px 8px',
              fontSize: '13px',
              fontFamily: 'var(--font-sans)',
              cursor: 'pointer',
              transition: 'all 0.2s ease',
            }}
          >
            {cmd.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default CommandPanel;