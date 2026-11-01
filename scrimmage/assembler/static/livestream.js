// The live stream client: the page's WebSocket to the simulation hub (scrimmage/assembler/stream.py).
//
// Binary frames are little-endian and packed: u32 seq, u8 source (0 live, 1 run), f64 t, u16 n, then n * 7 float32
// (x, y, z, qw, qx, qy, qz per mover, in the view's `movers` order). Text messages are JSON {type: "state"|"notice"}.
// The first message sent is {token}. Events (detail = the decoded payload): frame, state, notice, open, close.
// The page keeps one connection: sharedStream() opens it on first use (the Drive tab, or the run panel's Watch).

import { api, sessionToken } from "./app.js";

const HEADER = 15;   // 4 + 1 + 8 + 2

export function decodeFrame(buf) {
  const dv = new DataView(buf);
  const n = dv.getUint16(13, true);
  const poses = new Float32Array(n * 7);
  for (let i = 0; i < poses.length; i++) poses[i] = dv.getFloat32(HEADER + 4 * i, true);   // unaligned-safe
  return { seq: dv.getUint32(0, true), source: dv.getUint8(4), t: dv.getFloat64(5, true), n, poses };
}

const BACKOFF_MIN = 500, BACKOFF_MAX = 5000;   // ms

export class LiveStream extends EventTarget {
  constructor(port, token) {
    super();
    this.port = port;
    this.token = token;
    this.closed = false;
    this.ws = null;
    this.delay = BACKOFF_MIN;
    this.timer = null;
    this.connect();
  }

  connect() {
    if (this.closed) return;
    const ws = new WebSocket(`ws://${location.hostname}:${this.port}/`);
    ws.binaryType = "arraybuffer";
    this.ws = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ token: this.token }));
      this.delay = BACKOFF_MIN;
      this.emit("open", null);
    };
    ws.onmessage = (e) => {
      if (e.data instanceof ArrayBuffer) {
        if (e.data.byteLength >= HEADER) this.emit("frame", decodeFrame(e.data));
        return;
      }
      let msg;
      try { msg = JSON.parse(e.data); } catch { return; }
      if (msg && (msg.type === "state" || msg.type === "notice")) this.emit(msg.type, msg);
    };
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null;
      this.emit("close", null);
      if (!this.closed) {
        this.timer = setTimeout(() => this.connect(), this.delay);
        this.delay = Math.min(this.delay * 2, BACKOFF_MAX);
      }
    };
  }

  emit(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { detail }));
  }

  get isOpen() {
    return !!this.ws && this.ws.readyState === WebSocket.OPEN;
  }

  // Returns false (and sends nothing) when the socket is not open.
  send(obj) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  close() {
    this.closed = true;
    clearTimeout(this.timer);
    if (this.ws) this.ws.close();
  }
}

let shared = null;

// The page's one LiveStream (a promise; null when /api/live/info can't be reached, and then asked again next time).
// `port`: the stream's port when the caller already has /api/live/info.
export function sharedStream(port = null) {
  if (!shared) {
    shared = (async () => {
      let p = port;
      if (p === null) {
        const { status, data } = await api("GET", "/api/live/info");
        if (status !== 200) { shared = null; return null; }
        p = data.port;
      }
      return new LiveStream(p, sessionToken());
    })();
  }
  return shared;
}
