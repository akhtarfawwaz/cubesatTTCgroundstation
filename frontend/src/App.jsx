import { useState, useEffect, useRef } from 'react';
import { io } from 'socket.io-client';
import Header from './components/Header';
import TelemetryCards from './components/TelemetryCards';
import OrientationDisplay from './components/OrientationDisplay';
import GPSPanel from './components/GPSPanel';
import TelemetryChart from './components/TelemetryChart';
import LinkQualityPanel from './components/LinkQualityPanel';
import CommandPanel from './components/CommandPanel';
import AckLog from './components/AckLog';

const SOCKET_URL = 'http://127.0.0.1:5000';

// Fallback only — the backend sends the real value in station_config, so the
// dashboard and the receiver firmware cannot drift apart on what "quiet" means.
const DEFAULT_STALE_AFTER_S = 8;

function App() {
  const [connected, setConnected] = useState(false);
  const [telemetry, setTelemetry] = useState(null);
  const [history, setHistory] = useState([]);
  const [logs, setLogs] = useState([]);
  const [linkStats, setLinkStats] = useState(null);
  const [linkEvents, setLinkEvents] = useState([]);
  const [serialStatus, setSerialStatus] = useState(null);
  const [staleAfter, setStaleAfter] = useState(DEFAULT_STALE_AFTER_S);
  const [pendingCommand, setPendingCommand] = useState(null);
  const [secondsSincePacket, setSecondsSincePacket] = useState(null);

  const socketRef = useRef(null);
  const lastRxAtRef = useRef(null);

  useEffect(() => {
    const socket = io(SOCKET_URL);
    socketRef.current = socket;

    socket.on('connect', () => setConnected(true));
    socket.on('disconnect', () => setConnected(false));

    socket.on('station_config', (cfg) => {
      if (cfg?.stale_after_s) setStaleAfter(cfg.stale_after_s);
    });

    socket.on('serial_status', (status) => setSerialStatus(status));

    socket.on('telemetry', (data) => {
      setTelemetry(data);
      lastRxAtRef.current = Date.now();
      setSecondsSincePacket(0);
      setHistory((prev) => {
        const updated = [...prev, data];
        return updated.slice(-30); // keep last 30 points for chart
      });
    });

    socket.on('link_stats', (data) => setLinkStats(data));

    socket.on('link_event', (event) => {
      setLinkEvents((prev) =>
        [{ ...event, time: new Date().toLocaleTimeString() }, ...prev].slice(0, 20)
      );
    });

    socket.on('command_ack', (data) => {
      // 'pending' is the echo that the uplink was queued on the receiver; the
      // command is only resolved once the satellite echoes it back in a
      // downlink, or the backend gives up waiting.
      if (data.status === 'pending') {
        setPendingCommand({ cmd_type: data.cmd_type, value: data.value });
        return;
      }
      setPendingCommand(null);
      setLogs((prev) =>
        [{ ...data, time: new Date().toLocaleTimeString() }, ...prev].slice(0, 20)
      );
    });

    return () => {
      socket.disconnect();
      socketRef.current = null;
    };
  }, []);

  // Drives the "seconds since last packet" readout. Without it the dashboard
  // keeps rendering the last packet as if it were current when the link dies.
  useEffect(() => {
    const id = setInterval(() => {
      setSecondsSincePacket(
        lastRxAtRef.current === null ? null : (Date.now() - lastRxAtRef.current) / 1000
      );
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const sendCommand = (commandPayload) => {
    socketRef.current?.emit('command', commandPayload);
  };

  let linkState;
  if (!connected) linkState = 'offline';
  else if (serialStatus && serialStatus.state !== 'open') linkState = 'serial_down';
  else if (secondsSincePacket === null || secondsSincePacket > staleAfter) linkState = 'no_signal';
  else linkState = 'live';

  const live = linkState === 'live';

  return (
    <div style={{ maxWidth: '1200px', margin: '0 auto', padding: '24px' }}>
      <Header
        linkState={linkState}
        serialStatus={serialStatus}
        packetId={telemetry?.p_id}
        secondsSincePacket={secondsSincePacket}
      />

      <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '20px', marginTop: '20px' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <TelemetryCards telemetry={telemetry} />
          <TelemetryChart history={history} />
          <OrientationDisplay telemetry={telemetry} />
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          <LinkQualityPanel
            telemetry={telemetry}
            stats={linkStats}
            history={history}
            events={linkEvents}
            live={live}
          />
          <GPSPanel telemetry={telemetry} />
          <CommandPanel
            onSend={sendCommand}
            pending={pendingCommand}
            disabled={linkState === 'offline' || linkState === 'serial_down'}
          />
          <AckLog logs={logs} />
        </div>
      </div>
    </div>
  );
}

export default App;
