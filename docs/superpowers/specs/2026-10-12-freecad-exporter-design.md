# FreeCAD exporter (sub-project 4b): design

## Context

Sub-project 4a (merged) made robots out of parts: `[[part]]` entries name a stock part or a part folder
(`part.toml` plus meshes, format 1, documented in `docs/parts.md`), and Scrimmage assembles them into a MuJoCo
model and device bindings. Students can already build robots from stock parts. This sub-project lets a teammate
turn a mechanism modeled in FreeCAD into a part folder.

The team uses FreeCAD (and Inventor, exporting STEP into FreeCAD). FreeCAD 1.1.1 is installed here as a snap
(`freecad`, `freecad.cmd`; user Mod folder `~/snap/freecad/common/Mod`). A probe confirmed that headless
`freecad.cmd` can build and read FreeCAD 1.x Assemblies: joints expose `JointType` (Fixed, Revolute, Cylindrical,
Slider, Ball, Distance, Parallel, Perpendicular, Angle, RackPinion, Screw, Gears, Belt), `Reference1/2`,
`Placement1/2` (joint frames; z is the joint axis), `AngleMin/Max`, `LengthMin/Max` with enable flags,
`Distance`/`Distance2` (radii for gears, belts and rack-and-pinion) and `Suppressed`.

FreeCAD's Python (3.12, its own environment) can't import `scrimmage` (which needs MuJoCo).

Decisions made with the user:

- Approach A: a self-contained FreeCAD add-on (workbench + task panel + headless export functions).
- Mass: CAD volume × density (the FreeCAD material's density when assigned, aluminum otherwise), with a typed
  mass override per component.
- Mount points: picked in the panel (a face, circular edge or hole) and any Local Coordinate System named
  `mount_<name>`.
- The add-on runs on Linux, Windows and macOS; Scrimmage stays Linux-only.
- Closed linkages (four-bars) are supported through a new `[[loop]]` key in `part.toml`.

## Goals

1. A teammate opens a mechanism Assembly in FreeCAD, marks motors, sensors, mounts, mass overrides and contact
   bodies in a panel, and exports a part folder that `[[part]] path = "..."` loads unchanged.
2. Re-exporting after a CAD change keeps the panel choices; choices whose joint or component disappeared are
   reported, never silently moved.
3. Every export problem names the joint, component or slot and the fix.
4. The same export runs headless (`freecad.cmd`), so it is tested automatically and usable from a Linux CLI.
5. `part.toml` gains `[[loop]]` and `inertia_rot` (format 1, additive), supported end to end in scrimmage.

## Non-goals

- An Addon Manager listing (zip and `scrimmage freecad install` for now).
- Reading robot placement from a full-robot assembly (each part's placement stays in the robot config).
- Inventor files directly (STEP into FreeCAD first).
- Cylindrical, Ball, Distance, Parallel, Perpendicular, Angle and Screw joints; loops closed through Slider
  joints.
- Full inertia tensors in `part.toml` (principal axes plus `inertia_rot` express them exactly).
- Automated tests of the GUI panel itself (its logic lives in tested headless functions).

## Design

### Layout

```
freecad/Scrimmage/                 the FreeCAD workbench (pure Python, FreeCAD modules + stdlib only)
  package.xml, InitGui.py, Init.py, icons/
  scrimmage_part/
    read.py         Assembly -> extracted model (components, groups, joints, couplings, loops, mounts)
    mass.py         volumes, densities, overrides -> mass, COM, principal inertia per body
    tree.py         rigid groups + joints -> body tree, loop-closing joints, frames
    settings.py     the hidden ScrimmagePart object: slots, overrides, contact marks, export folder
    export.py       export(doc_or_path, out_dir) -> summary; writes part.toml + meshes atomically
    toml_writer.py  byte-identical copy of scrimmage/parts/toml_writer.py
    motors.py       generated list of motor names (from scrimmage/motors.py)
    panel.py        the task panel (thin: calls the functions above)
scrimmage/parts/toml_writer.py    the part.toml writer, standard library only (moved out of model.py)
scrimmage/freecad_tools.py         `scrimmage freecad install|zip|export`
```

### From an Assembly to a part

- One Assembly is one part; the part's `name` is the Assembly's label.
- **Rigid groups:** components joined by Fixed joints, or not jointed to anything, merge into one group. Each
  group is a `[[body]]`. A component not connected to the grounded group by any joint merges into the root group
  with a warning.
- **Root:** the group containing the grounded component (FreeCAD's grounded joint, "Toggle grounded"). Its body
  frame is the Assembly origin. No grounded component is an error.
- **Tree:** breadth-first from the root over Revolute and Slider joints. A joint's parent is the body nearer the
  root; the child body's frame is the joint frame (`Placement` on the child side), so the joint sits at the child
  origin with its axis along `z` (`hinge = "z"` / `slide = "z"`). Limits (when enabled) become `range` (degrees for
  hinges, meters for slides, converted from FreeCAD's mm).
- **Loops:** a Revolute joint whose two bodies are already connected in the tree closes a loop. It becomes a
  `[[loop]]` pin at the joint origin. A Slider that closes a loop is an error.
- **Couplings:** Gears → ratio −r₁/r₂ (b turns opposite a), Belt → +r₁/r₂, RackPinion → rack meters per pinion
  radian = r (pinion radius, meters). For gears and belts a and b are the two coupled joints in FreeCAD's reference
  order; for rack and pinion a is the pinion's Revolute and b the rack's Slider. A coupling between joints that aren't tree joints is an error.
- **Unsupported joints** (Cylindrical, Ball, Distance, Parallel, Perpendicular, Angle, Screw): error naming the
  joint and suggesting Revolute, Slider or Fixed. Suppressed joints are ignored.
- **Geometry:** each body's components' shapes, in the body frame, tessellated (linear deflection 0.5 mm, angular
  0.3 rad), converted to meters, written as `meshes/<body>.stl` (binary). One mesh geom per body.
- **Mass:** per component, volume × density, where density is the FreeCAD 1.x material's `Density` when one is
  assigned, else aluminum 2700 kg/m³; a typed mass override replaces that component's mass (its volume still
  shapes its inertia). Per body: total mass, COM and inertia tensor about the COM combined from the components;
  written as `mass`, `com`, the principal moments as `inertia` and their axes as `inertia_rot`. A body whose mass
  is zero (no volume, no override) is an error.
- **Contact:** body geoms are `collide = false` by default. A body marked **wheel** gets, instead of a colliding
  mesh, a collision cylinder fitted to the wheel (radius and width from the shape's extent about the body's
  joint axis), `friction` from the panel (default 1.2), `condim = 4`; its mesh stays as a non-colliding visual.
  A body marked **collide** collides through its mesh (MuJoCo uses the mesh's convex hull; the panel says so).

### Slots and mounts

- **Motor slot** (on a tree joint): name, motor (from Scrimmage's motor list), count, and gearing given as a
  gear ratio (motor turns per joint turn) — for a Slider also a spool/pinion radius, converted to motor radians
  per meter as the stock elevator does (`ratio / radius`); invert; efficiency (default 1).
  A motor on a loop-closing joint is an error naming the tree joint to use.
- **Encoder slot:** name, joint, and "on the joint" (ratio 1) or "on the motor shaft" (ratio = the joint's motor
  gear ratio); invert.
- **Gyro slot:** name, body.
- **Mounts:** picked faces (center of the face, z along its outward normal), circular edges and holes (center,
  z along the axis); every LCS named `mount_<name>`. Each mount is expressed in its body's frame. Duplicate mount
  names are an error.

### The panel and saved choices

A workbench "Scrimmage" with one command, "Scrimmage part", opening a task panel for the active Assembly:
Bodies (groups, computed mass, per-component mass override, wheel/collide marks, wheel friction; selecting a row
highlights the group), Joints (type, limits, tree or loop; add/edit motor and encoder slots), Gyro, Mounts (add
by picking; LCS mounts listed), Export (folder, remembered; summary of bodies, joints, slots, mounts, total mass;
problems listed with fixes).

Choices are stored on a hidden `App::FeaturePython` object `ScrimmagePart` in the document, keyed by joint and
component internal names (not labels), with a format version. Missing keys on re-export produce warnings
("slot 'pivot' was on joint 'Revolute003', which no longer exists"); the slot is dropped from that export.

### Headless export and Scrimmage commands

- `scrimmage_part.export.export(fcstd_path_or_doc, out_dir) -> dict` (summary with warnings); raises
  `ExportError` (all problems listed) without writing anything. Output is written to a temporary folder next to
  `out_dir` and renamed into place; an existing `out_dir` is replaced only if it holds a `part.toml` (never an
  unrelated folder).
- `scrimmage freecad install [--to DIR]`: copies `freecad/Scrimmage` into FreeCAD's user Mod folder (snap
  `~/snap/freecad/common/Mod`, Flatpak `~/.var/app/org.freecad.FreeCAD/data/FreeCAD/Mod`, else
  `~/.local/share/FreeCAD/Mod`), replacing an older copy.
- `scrimmage freecad zip [-o FILE]`: a zip of the workbench for Windows/macOS teammates (unzip into the Mod
  folder shown under FreeCAD's Help → About → Copy to clipboard "User data directory"/Mod).
- `scrimmage freecad export FILE.FCStd OUT_DIR`: runs `freecad.cmd` (or `FreeCADCmd`) with the add-on on its
  path and prints the summary; exit 2 with the listed problems on failure, or when FreeCAD isn't found.

### `part.toml` additions (format 1, additive)

- `[[loop]]`: `body1`, `body2`, `pos` (meters, the pin location in `body1`'s frame). The assembler adds a MuJoCo
  `connect` equality between the two (prefixed) bodies at that point; validation checks both bodies exist and
  differ. Docs explain that the tree hinges plus the pin keep a planar linkage's motion.
- `inertia_rot` on `[[body]]`: roll, pitch, yaw (degrees) of the principal axes in the body frame; requires
  `inertia`; becomes the body's inertial orientation. This supersedes 4a ruling R10's "diagonal in the body
  frame" wording: `inertia` stays diagonal, now in the frame `inertia_rot` gives.
- Round trip, writer, docs (`docs/parts.md`) updated.

### Keeping the copies in sync

`scrimmage/parts/toml_writer.py` imports only the standard library. A Scrimmage test asserts the add-on's
`toml_writer.py` is byte-identical and that the add-on's `motors.py` lists exactly `scrimmage.motors.MOTORS`.
A script (`freecad/sync.py`) regenerates both copies.

## Failure handling

| Situation | Behavior |
|---|---|
| No grounded component | error: "ground one component (right-click → Toggle grounded)" |
| Unsupported joint type | error naming the joint and type; suggests Revolute, Slider or Fixed |
| Slider closes a loop | error naming the joint |
| Coupling on a non-tree joint, or with a zero radius | error naming the coupling |
| Motor on a loop-closing joint | error naming the tree joint to use |
| Body with zero mass | error naming the body's components; suggests a mass override |
| Component not connected to anything | warning; merged into the root body |
| Saved choice whose joint/component is gone | warning; dropped from this export |
| Duplicate slot or mount names | error |
| Export folder exists and isn't a part folder | error; nothing written |
| FreeCAD not found (`scrimmage freecad export`) | exit 2 explaining how to install it or point to it |

## Testing

- `tests/freecad/make_fixtures.py` builds fixture `.FCStd` files headless (no hand-made CAD binaries): a
  single-pivot arm with a motor slot and an LCS mount; a 2-stage slider with a belt coupling; a four-bar; a
  two-wheel base with wheel marks and a gyro; and one bad fixture per error row above.
- A `freecad` pytest marker (skipped when `freecad.cmd`/`FreeCADCmd` isn't found or `SCRIMMAGE_SKIP_FREECAD=1`):
  export each fixture, load it with `load_part` and the assembler, and assert body count, joint axes and ranges,
  coupling ratios, loop pins, masses (volume × density, overrides), COM and principal inertia, mounts (picked and
  LCS), slot translation, and each error message.
- Physics: the exported four-bar's coupler and rocker angles follow the closed-form four-bar relation when the
  crank is driven; an exported single-pivot arm matches the stock `arm` holding voltage within 5 %.
- Scrimmage unit tests (no FreeCAD): `[[loop]]` and `inertia_rot` validation, round trip and assembly (a
  hand-written four-bar `part.toml`), writer and motor-list sync.
- CI: a `freecad` job installs FreeCAD (AppImage, pinned version) and runs `-m freecad`; if FreeCAD can't run on
  the runner, the job is marked allowed-to-fail and the spec's tests still run locally.
- The panel is checked by hand against the fixtures (a short checklist in the docs).

## Docs

- `docs/freecad.md`: installing the add-on (Linux command, zip for Windows/macOS), preparing an Assembly
  (grounding, Revolute/Slider/Fixed, couplings, LCS mounts), the panel walkthrough, re-exporting, troubleshooting
  each error.
- `docs/parts.md`: `[[loop]]` and `inertia_rot`.
- README: a short "Parts from FreeCAD" section linking both.
