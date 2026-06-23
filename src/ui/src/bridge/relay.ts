import type { RelayInboundMessage, RelayOutboundMessage } from "../types/relay";

export interface RelayClient {
  connect(port: number): void;
  close(): void;
  send(message: RelayOutboundMessage): void;
}

export function createRelayClient(onMessage: (message: RelayInboundMessage) => void): RelayClient {
  let socket: WebSocket | null = null;
  let stopped = false;
  let activePort = 8765;

  function connect(port: number) {
    activePort = port;
    stopped = false;
    if (socket && socket.readyState <= WebSocket.OPEN) return;
    socket = new WebSocket(`ws://127.0.0.1:${activePort}`);
    socket.onmessage = (event) => {
      const parsed = JSON.parse(String(event.data)) as RelayInboundMessage;
      onMessage(parsed);
    };
    socket.onclose = () => {
      socket = null;
      if (!stopped) window.setTimeout(() => connect(activePort), 2000);
    };
    socket.onerror = () => socket?.close();
  }

  return {
    connect,
    close() {
      stopped = true;
      socket?.close();
      socket = null;
    },
    send(message: RelayOutboundMessage) {
      if (socket?.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify(message));
      }
    },
  };
}
