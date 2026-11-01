// Geometry helpers shared by the Robot tab's view (view3d.js) and the live view (live3d.js).
//
// MuJoCo's world is z-up and three.js is y-up by default. Scenes keep MuJoCo's frame (positions and quaternions are
// copied as they are) and cameras are told that +z is up instead.

import * as THREE from "three";

export function decode(b64, Type) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Type(bytes.buffer);
}

export function meshGeometry(m) {
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(decode(m.vert, Float32Array), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(decode(m.face, Int32Array)), 1));
  g.computeVertexNormals();
  return g;
}

// MuJoCo sizes: box half-sizes; cylinder and capsule radius + half-length along z; sphere radius;
// ellipsoid radii. three's cylinder and capsule run along y, so they are turned onto z. `coarse`: a sphere with
// few segments, for the test range's hundreds of balls (a robot's spheres keep the full count).
export function primitive(type, s, coarse = false) {
  let g;
  switch (type) {
    case "box": return new THREE.BoxGeometry(2 * s[0], 2 * s[1], 2 * s[2]);
    case "sphere": return coarse ? new THREE.SphereGeometry(s[0], 12, 8) : new THREE.SphereGeometry(s[0], 24, 16);
    case "ellipsoid": g = new THREE.SphereGeometry(1, 24, 16); g.scale(s[0], s[1], s[2]); return g;
    case "cylinder": g = new THREE.CylinderGeometry(s[0], s[0], 2 * s[1], 32); g.rotateX(Math.PI / 2); return g;
    case "capsule": g = new THREE.CapsuleGeometry(s[0], 2 * s[1], 8, 24); g.rotateX(Math.PI / 2); return g;
    default: return null;   // plane and anything unknown: not drawn
  }
}

export const setPose = (obj, pos, quat) => {
  obj.position.set(pos[0], pos[1], pos[2]);
  obj.quaternion.set(quat[1], quat[2], quat[3], quat[0]);   // MuJoCo quats are w, x, y, z
};

// The standard part material; rgba is MuJoCo's [r, g, b, a] (linear-looking sRGB values as authored).
export function geomMaterial(rgba, isMesh) {
  rgba = rgba || [0.6, 0.6, 0.6, 1];
  return new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(rgba[0], rgba[1], rgba[2], THREE.SRGBColorSpace),
    roughness: 0.75, metalness: 0.05, flatShading: !!isMesh,
    transparent: rgba[3] < 1, opacity: rgba[3],
  });
}

export function buildFloor(f) {
  const [cx, cy, cz] = f.pos, [hx, hy] = f.size;
  const floor = new THREE.Group();
  const plane = new THREE.PlaneGeometry(2 * hx, 2 * hy);   // in the xy plane, facing +z
  // pushed back in depth so the grid lines and parts resting on the floor never fight with it
  const pmat = new THREE.MeshStandardMaterial({ color: 0xd4d8dd, roughness: 1, metalness: 0,
    polygonOffset: true, polygonOffsetFactor: 2, polygonOffsetUnits: 4 });
  const p = new THREE.Mesh(plane, pmat);
  p.position.set(cx, cy, cz);
  floor.add(p);
  // half-meter grid lines across the floor, in cell-long pieces: some GL implementations drop a whole line
  // when one end is behind the camera, and the field's lines are 16 m long
  const pts = [];
  const step = 0.5;
  const xs = [], ys = [];
  for (let x = Math.ceil((cx - hx) / step) * step; x <= cx + hx + 1e-9; x += step) xs.push(x);
  for (let y = Math.ceil((cy - hy) / step) * step; y <= cy + hy + 1e-9; y += step) ys.push(y);
  const yc = [cy - hy, ...ys.filter((y) => y > cy - hy + 1e-9 && y < cy + hy - 1e-9), cy + hy];
  const xc = [cx - hx, ...xs.filter((x) => x > cx - hx + 1e-9 && x < cx + hx - 1e-9), cx + hx];
  for (const x of xs) for (let i = 1; i < yc.length; i++) pts.push(x, yc[i - 1], cz, x, yc[i], cz);
  for (const y of ys) for (let i = 1; i < xc.length; i++) pts.push(xc[i - 1], y, cz, xc[i], y, cz);
  const lg = new THREE.BufferGeometry();
  lg.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  const lmat = new THREE.LineBasicMaterial({ color: 0xa9b0b8, transparent: true, opacity: 0.7 });
  floor.add(new THREE.LineSegments(lg, lmat));
  return { group: floor, disposables: [plane, pmat, lg, lmat] };
}
