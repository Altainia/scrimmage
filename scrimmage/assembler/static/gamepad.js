// Keyboard and gamepad input for driving: WPILib XboxController-shaped values {axes: [6], buttons: [10], povs: [-1]}.
//
// Keyboard: W/S axis 1 (W = -1), A/D axis 0 (A = -1), I/K axis 5 (I = -1), J/L axis 4 (J = -1), keys 1-8 = buttons
// 1-8 (indices 0-7). `pressed` holds KeyboardEvent.code names.

export const KEYBOARD = "keyboard";

const AXIS_KEYS = [["KeyA", "KeyD", 0], ["KeyW", "KeyS", 1], ["KeyJ", "KeyL", 4], ["KeyI", "KeyK", 5]];   // [-, +, axis]

// The keys that drive the robot (KeyboardEvent.code names).
const DRIVING_KEYS = new Set([...AXIS_KEYS.flatMap(([neg, pos]) => [neg, pos]), ...Array.from({ length: 8 }, (_, i) => `Digit${i + 1}`)]);
export const isDrivingKey = (code) => DRIVING_KEYS.has(code);

export function keyboardInputs(pressed) {
  const axes = [0, 0, 0, 0, 0, 0];
  for (const [neg, pos, i] of AXIS_KEYS) axes[i] = (pressed.has(pos) ? 1 : 0) - (pressed.has(neg) ? 1 : 0);
  const buttons = new Array(10).fill(false);
  for (let i = 0; i < 8; i++) buttons[i] = pressed.has(`Digit${i + 1}`);
  return { axes, buttons, povs: [-1] };
}

const STANDARD_BUTTONS = [0, 1, 2, 3, 4, 5, 8, 9, 10, 11];   // WPILib buttons 1-10
const DPAD = { up: 12, down: 13, left: 14, right: 15 };

const down = (b) => !!b && (b.pressed || b.value > 0.5);

function dpadPov(buttons) {
  const up = down(buttons[DPAD.up]), dn = down(buttons[DPAD.down]), lf = down(buttons[DPAD.left]), rt = down(buttons[DPAD.right]);
  const v = (up ? 1 : 0) - (dn ? 1 : 0), x = (rt ? 1 : 0) - (lf ? 1 : 0);
  if (!v && !x) return -1;
  return { "1,0": 0, "1,1": 45, "0,1": 90, "-1,1": 135, "-1,0": 180, "-1,-1": 225, "0,-1": 270, "1,-1": 315 }[`${v},${x}`];
}

export function gamepadInputs(gp) {
  const b = gp.buttons;
  if (gp.mapping === "standard") {
    const val = (i) => (b[i] ? b[i].value : 0);
    return {
      axes: [gp.axes[0] || 0, gp.axes[1] || 0, val(6), val(7), gp.axes[2] || 0, gp.axes[3] || 0],
      buttons: STANDARD_BUTTONS.map((i) => down(b[i])),
      povs: [dpadPov(b)],
    };
  }
  return { axes: Array.from(gp.axes), buttons: Array.from(b, down), povs: [-1] };
}

const zeroInputs = () => ({ axes: [0, 0, 0, 0, 0, 0], buttons: new Array(10).fill(false), povs: [-1] });

// A key pressed in a text box, select or contenteditable belongs to that control, not to the robot.
export function isEditable(el) {
  if (!el || !el.tagName) return false;
  if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  return el.tagName === "INPUT" && !["checkbox", "radio", "button", "submit", "reset", "range", "color"].includes(el.type);
}

// Maps ports to input sources (KEYBOARD or a gamepad index) and polls them. Dispatches "gamepad-lost" (detail =
// the port) when an assigned gamepad disappears; that port then reads zeros. Key presses aimed at an editable
// control are ignored (except driving keys on a select, which has no use for them but type-ahead), and so are those
// `accept(event)` (optional) turns down; releases always count.
export class InputRouter extends EventTarget {
  constructor({ accept = null } = {}) {
    super();
    this.pressed = new Set();
    this.ports = new Map();    // port -> source
    this.onDown = (e) => {
      const forSelect = e.target && e.target.tagName === "SELECT" && isDrivingKey(e.code);
      if ((isEditable(e.target) && !forSelect) || (accept && !accept(e))) return;
      this.pressed.add(e.code);
    };
    this.onUp = (e) => this.pressed.delete(e.code);
    this.onBlur = () => this.pressed.clear();
    window.addEventListener("keydown", this.onDown);
    window.addEventListener("keyup", this.onUp);
    window.addEventListener("blur", this.onBlur);
  }

  assign(port, source) {
    if (source === null || source === undefined) this.ports.delete(port);
    else this.ports.set(port, source);
  }

  assignments() {
    return Object.fromEntries(this.ports);
  }

  poll() {
    const out = {};
    const pads = navigator.getGamepads ? navigator.getGamepads() : [];
    for (const [port, source] of this.ports) {
      if (source === KEYBOARD) { out[String(port)] = keyboardInputs(this.pressed); continue; }
      const gp = pads[source];
      if (gp && gp.connected !== false) { out[String(port)] = gamepadInputs(gp); continue; }
      out[String(port)] = zeroInputs();
      this.ports.delete(port);
      this.dispatchEvent(new CustomEvent("gamepad-lost", { detail: port }));
    }
    return out;
  }

  dispose() {
    window.removeEventListener("keydown", this.onDown);
    window.removeEventListener("keyup", this.onUp);
    window.removeEventListener("blur", this.onBlur);
    this.pressed.clear();
    this.ports.clear();
  }
}
