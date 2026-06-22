import { useState, useEffect } from 'react';
import { io } from 'socket.io-client';
import Header from './components/Header';
import TelemetryCards from './components/TelemetryCards';
import OrientationDisplay from './components/OrientationDisplay';
import GPSPanel from './components/GPSPanel';
import TelemetryChart from './components/TelemetryChart';
import CommandPanel from './components/CommandPanel';
import AckLog from './components/AckLog';

const SOCKET_URL = 'http://127.0.0.1:5000';

function App() {
  const [connected, setConnected] = useState(false);
  const [telemetry, setTelemetry] = useState(null);
  const [history, setHistory] = useState([]);
  const [logs, setLogs] = useState([]);

  useEffect(() => {
    const socket = io(SOCKET_URL);

    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));

    socket.on('telemetry', (data) => {
      setTelemetry(data);
      setHistory((prev) => {
        const updated = [...prev, data];
        return updated.slice(-30); // keep last 30 points for chart
      });
    });

    socket.on('command_ack', (data) => {
      setLogs((prev) => [
        { ...data, time: new Date().toLocaleTimeString() },
        ...prev,
      ].slice(0, 20));
    });

    return () => socket.disconnect();
  }, []);

  const sendCommand = (commandPayload) => {
    const socket = io(SOCKET_URL);
    socket.emit('command', commandPayload);
  };

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <Header connected={connected} packetId={telemetry?.packet_id} />

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '20px', marginTop: '20px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <TelemetryCards telemetry={telemetry} />
          <TelemetryChart history={history} />
          <OrientationDisplay telemetry={telemetry} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <GPSPanel telemetry={telemetry} />
          <CommandPanel onSend={sendCommand} />
          <AckLog logs={logs} />
        </div>
      </div>
    </div>
  );
}

export default App;