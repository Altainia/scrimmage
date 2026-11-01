# FreeCAD exporter

The Scrimmage add-on turns a FreeCAD **Assembly** (FreeCAD 1.1 or newer; tested on 1.1.1 and 1.1.3) into a
[part folder](parts.md): a `part.toml` plus one mesh per rigid body. You ground one component, join the others with
joints, say which joints have motors, and export. The folder then goes into a robot config as
`[[part]] path = "..."`, like any other part. The add-on is pure Python; it reads the Assembly, it never changes
your geometry.

Contents: [Install](#install), [Prepare the Assembly](#prepare-the-assembly), [The panel](#the-panel),
[Re-exporting](#re-exporting), [Command line](#command-line), [Troubleshooting](#troubleshooting),
[Panel checklist](#panel-checklist).

## Install

The add-on ships with the Scrimmage git checkout only (it is not in the pip wheel). Install Scrimmage from the
checkout with `pip install -e <checkout>`, then:

- **Linux:** `scrimmage freecad install` copies the add-on into FreeCAD's user Mod folder and prints where. It
  looks, in order, for the snap (`~/snap/freecad/common/Mod`), the Flatpak
  (`~/.var/app/org.freecad.FreeCAD/data/FreeCAD/Mod`) and `~/.local/share/FreeCAD/Mod`, and uses the first that
  exists (the last one is created if none does). `scrimmage freecad install --to <Mod folder>` installs somewhere
  else, for an AppImage or a nonstandard setup. Re-running it replaces the old copy.
- **Windows and macOS:** `scrimmage freecad zip` writes `Scrimmage.zip` (`-o path` to name it). Unzip it so that
  the `Scrimmage` folder sits inside FreeCAD's user `Mod` folder: usually `%APPDATA%\FreeCAD\Mod` on Windows
  and `~/Library/Application Support/FreeCAD/Mod` on macOS (create `Mod` if it isn't there; FreeCAD 1.1 may use a
  versioned folder such as `FreeCAD/v1-1/Mod`). The sure way is to type `FreeCAD.getUserAppDataDir()` in
  FreeCAD's Python console and use `Mod` inside the folder it prints.

Restart FreeCAD. **Scrimmage** appears in the workbench selector. Update the add-on after pulling Scrimmage by
running the same command again.

The command-line export and the tests need a headless FreeCAD: `freecad.cmd` (snap), `FreeCADCmd` or `freecadcmd` on
`PATH`, or set `SCRIMMAGE_FREECAD_CMD` to its path.

## Prepare the Assembly

Build the mechanism in FreeCAD's Assembly workbench and **solve it** (the Assembly must be closed and solved when you
save and export; the exporter reads the saved placements and warns when joints don't line up).

- **Ground one component** (right-click it, Toggle grounded). It becomes the part's root body. Every other
  component must connect to it through joints; one that doesn't is merged into the root with a warning.
- **Joints.** Revolute (a hinge), Slider (a slide) and Fixed (welds two components into one rigid body) are
  exported as joints. **Gears, Belt and Rack and Pinion** become couplings between two joints; signs and ratios
  follow FreeCAD's own (set the pitch radii on the joint: its two radii, or just the radius for a rack and pinion).
  The two coupled joints must move relative to the same part (two pulleys on one frame, a pinion and a rack on one
  rail). A chain from a sprocket on the frame to a wrist on a moving arm can't be expressed yet: give the wrist's
  joint its own motor slot instead. A coupling's frame may sit anywhere on its joint's axis line.
  Any other type (Ball, Cylindrical, Parallel, ...) is an error: use Revolute, Slider or Fixed. Suppressed joints are
  ignored.
- **Creating joints:** in the Assembly workbench use Create Joint (Revolute, Slider, ...); to limit a joint, tick
  **Enable limits** in the joint's task panel (or the `EnableAngleMin/Max` and `EnableLengthMin/Max` properties).
- **Joint limits** come from the joint's min/max ("Enable limits"). They are measured from the pose the Assembly was
  saved in, which becomes the part's zero: encoders count from there. FreeCAD reads a saved angle between -180° and
  180°; it is shifted by whole turns into the limits (a 0° … 270° joint saved at 200° exports as -200° … 70°). A
  joint saved outside its limits is an error. A limit on one side only is exported with
  `-1e6` or `1e6` on the other side, which means no limit (see [Joints](parts.md#joints)).
- **Loops.** A closed chain, such as a four-bar or a slider-crank, works: the exporter picks a spanning tree and
  closes the loop with a `[[loop]]` pin at a Revolute joint (see [`[[loop]]`](parts.md#loop)). A loop made of
  Sliders only is an error. The CAD must really be closed: a loop whose ends are apart is an error.
  Motors and encoders go on tree joints, not on the joint that closes a loop: the exporter keeps the joints that
  carry slots in the tree whenever it can, and when it can't, the error names the tree joint to use.
- **Mass** is the volume of each component's solid times its density. The density is the FreeCAD material's;
  without a material it is aluminum (2700 kg/m^3). A material that has no density counts as aluminum too, with a
  warning. The panel's Override kg replaces a component's whole mass (it must be greater than 0), and its density
  is then ignored. Faces, open shells and broken (inside-out) solids have no volume, so they add no mass; the export
  warns, naming the component: give it an override (a sheet-metal skin, say) or repair it into a closed solid. The center of mass and the full inertia tensor of each body are exported (`com`, `inertia`,
  `inertia_rot`).
- **Meshes** are the visual geometry, written in meters in each body's frame. They don't collide by default
  (`collide = false`); mark a component in the panel to change that:
  - **wheel**: the body also gets a collision cylinder about its joint axis (radius and width from the shape) with
    friction 1.2 unless you set another. The body must turn on a Revolute joint. If the wheel's shape is more than
    1 mm off the joint axis the exporter warns that the cylinder is centered on the axis.
  - **collide**: the mesh collides, as its convex hull.
- **Mounts** are the places other parts attach (`attach = "<instance>.<mount>"`). Add one by picking a face, edge or
  cylinder in the 3D view (below), or by adding a coordinate system (LCS) whose label starts with `mount_`, such as
  `mount_top`; the part after the prefix is the mount's name. Mount names are letters, digits and `_`, unique in the
  part. An LCS inside a component, or attached to one, moves with that component; any other sits on the root body.

## The panel

Switch to the **Scrimmage** workbench and click **Scrimmage part**. A panel opens in the Tasks tab with five
sections (the [checklist](#panel-checklist) walks through each):

- **Bodies**: the rigid bodies and their computed masses. Per component: Override kg, Contact (`—`, wheel or
  collide) and wheel Friction. Selecting a row selects the component in the 3D view.
- **Joints**: every joint, its type, its role (tree, loop, coupling, fixed, suppressed or broken) and its limits.
  Add **motor** slots (fields Slot, Joint, Motor, Motors, Gear ratio, Spool/pulley radius mm (Slider joints only), Inverted,
  Efficiency) and **encoder** slots (Slot, Joint, Measures "the joint" or "the motor shaft", Inverted).
- **Gyro**: gyro slots, each on a component's body.
- **Mounts**: pick a flat face (the mount's z axis is its outward normal), a circular edge or a cylindrical face (its
  center and axis), then **Add from selection**. A circle or cylinder axis has no inside or outside, so it points
  along whichever of the component's own x, y or z it is closest to, positive side; rotate the mount in the robot
  config if you need the other way. LCS mounts are listed too.
- **Export**: choose the part folder and click **Export**. The box then shows the summary and any warnings, or, if
  something is wrong, the problems; nothing is written when there is a problem.

Slot names (motor, encoder and gyro slots) become the part's slot names, which your robot config binds to device
IDs: `ids = { pivot = "TalonFX:5" }`.

Everything you set in the panel is saved **inside the `.FCStd` file** (a hidden object named `ScrimmagePart`), keyed by
FreeCAD's internal names, so renaming parts or joints in the tree is safe. Settings that point at something you deleted
produce a warning on the next export ("... no longer exists; dropped") and are dropped. Each change in the panel is
one Edit > Undo step. Undoing or redoing a settings change closes the panel (the Report view says "settings
changed outside the panel"); re-open it to see the restored settings.

## Use the exported part

The exported folder is an ordinary [part folder](parts.md). Put it in a robot config as a `[[part]]`
([Putting parts in a robot config](parts.md#putting-parts-in-a-robot-config) explains each key). This config was
checked against a real export of an arm Assembly with one motor slot `pivot` and one encoder slot `encoder`
(parts/arm next to the config):

```toml
[[part]]
name = "drive"
stock = "chassis"
ids = { gyro = "Pigeon2:13" }

[[part]]
name = "arm"
path = "parts/arm"            # the exported folder, relative to this config
attach = "drive.top"          # <instance>.<mount or body> of the part it sits on
ids = { pivot = "TalonFX:5", encoder = { channel_a = 0, counts_per_rev = 2048 } }
```

- `ids` is keyed by the slot names you chose in the panel (`scrimmage parts` lists the stock parts' slots; for your
  own part, they are the `slot` values in its `part.toml`).
- Mount names come from the panel's Mounts list and from `mount_<name>` coordinate systems. Another part attaches
  to one with `attach = "arm.<mount>"` (the arm's `top` mount is `arm.top`); a body name works too.
- Check the result with `scrimmage model <config>` (it prints the assembled MJCF, or the error naming the part and
  key) and `scrimmage parts` (the stock parts and their slots and mounts).

## Re-exporting

Change the CAD, re-solve, save, open the panel and click Export again. The panel remembers the folder. Exporting
into a folder that already holds a `part.toml` replaces that part folder (the new one is written next to it and
swapped in, so a failed export leaves the old one intact). A folder that holds other files but no `part.toml` is
refused. If the old folder holds a `scrimmage-edits.toml` (the edits you made to this part in the Part tab; see
[the Part tab](part-tab.md#parts-made-by-a-cad-plugin)), it is copied into the new folder, so exporting again does not
lose them. Edits about a body that is no longer in the Assembly come back as a warning when the part is loaded. The panel reads the Assembly when it opens, so re-open it after editing the Assembly.

## Command line

```bash
scrimmage freecad export my_robot.FCStd parts/arm
```

Exports the Assembly in `my_robot.FCStd` with headless FreeCAD, using the settings saved in the file (set them up
once in the panel). Documents the file links to (App::Link into another `.FCStd`) must sit where the links expect
them, normally next to it. FreeCAD opens the file where it is; a snap FreeCAD can only read non-hidden folders in
your home folder, so a file elsewhere (such as `/tmp`) is copied, together with the `.FCStd` files next to it,
before exporting. It prints a summary and any warnings:

```
exported part 'Assembly' to /path/parts/arm
  bodies (2): Base, Arm
  joints (1): Hinge
  loops: 0, couplings: 0
  slots: none
  mounts: top
  mass: 0.540 kg
```

On a problem it prints `scrimmage: can't export <file>:` with one `- ` line per problem and exits with status 2,
writing nothing. It also exits 2 when FreeCAD can't be found or fails. `scrimmage freecad install` and
`scrimmage freecad zip` are described under [Install](#install).

## Troubleshooting

Messages are shown in the panel's Export box, or by `scrimmage freecad export`. Names in quotes are the labels or
slot names from your file.

**Assembly and joints**

- `document '<name>' has no Assembly`: the active document has no Assembly object; build one in the Assembly workbench.
- `nothing is grounded: ground one part (right-click it, Toggle grounded) of <components>`: ground one component.
  With no components at all: `nothing is grounded: the Assembly has no parts; add parts to it and ground one`.
- `joint '<j>' is saved outside its limits; move it inside and save`: the Assembly's saved pose puts the joint past a
  limit (angles are tried a whole turn either way first); move the part inside the range, solve and save.
- `joint '<j>' has its minimum (<min>) at or above its maximum (<max>); set a minimum below the maximum`.
- `joint '<j>' is broken: one of its references is missing or isn't a part of the Assembly; edit the joint and pick its two parts again`:
  a referenced part was deleted or moved out of the Assembly.
- `joint '<j>' is a <type> joint, which scrimmage can't export: use Revolute, Slider or Fixed`: replace the joint (or
  use a Gears, Belt or Rack and Pinion joint on top of Revolute and Slider joints).
- `joint '<j>' joins <A> and <B>, which are both grounded: un-ground one of them`: a moving joint between two
  groups that are both grounded (or fixed to the ground) can't move anything; un-ground one of them.
- `joint '<j>' joins <A> and <B>, which are fixed together; remove it or the Fixed joint`: two components that a Fixed
  joint welds can't also have a moving joint between them.
- `slider '<j>' closes a loop of Sliders: a loop can only close at a Revolute`: change a joint in the loop to a
  Revolute, or break the loop.
- `loop at <j> isn't closed in the CAD (off by <n> mm)`: the chain's ends don't meet. Solve the Assembly in FreeCAD
  (or fix the geometry) and save.
- `grounded object '<g>' isn't a part of the Assembly; ignored` (warning): a grounding joint refers to something outside the Assembly.
- `not connected to the grounded part by any joint, so merged into it: <components>` (warning): add joints for them,
  or ignore it if they are fixed to the frame.
- `assembly not solved: joint '<j>' has its two ends <n> mm apart[ and its axes not aligned]; solve the Assembly in FreeCAD and save` (warning):
  the exported zero pose comes from the saved placements; solve and save.

**Couplings (Gears, Belt, Rack and Pinion)**

- `coupling '<c>' has a zero or negative radius: set its radii to the pitch radius` (`radius` for a rack and pinion).
- `coupling '<c>': can't tell which Revolute/Slider it drives on <part>; pick the same frame as that joint's`: the
  coupling's reference frame must lie on the axis line of the joint it drives (within 0.01 mm, axis parallel).
- `coupling '<c>' links '<a>' (moving relative to <A>) and '<b>' (moving relative to <B>): Scrimmage couplings link
  two joints that move relative to the same part, so a chain-driven wrist on a moving arm can't be expressed yet;
  drive '<j>' directly with its own motor slot`.
- `coupling '<c>' couples '<j>', which closes a loop: couple tree joints only`.
- `coupling '<c>' is on <part>, which '<j>' doesn't move (it moves <body>): put the coupling on the moving part`.
- `gears|belt '<c>' needs two Revolute joints, not <T1> '<a>' and <T2> '<b>'`.
- `rack and pinion '<c>' needs a Slider (rack) and a Revolute (pinion), not <T1> '<a>' and <T2> '<b>'`.
- `rack and pinion '<c>': its rack frame is on the pinion axis, so the rack's side can't be told; pick the rack's pitch line`.

**Mass, contact and meshes**

- `body '<b>': body made of '<components>' has no mass (no solid volume): give one of its components a mass override`:
  the body has no solid (an empty Part or a surface); set Override kg in the Bodies section.
- `material '<m>' on <part> has no density; used 2700 kg/m³ — assign a material with a density or set a mass override` (warning).
- `<part> has faces or shells that aren't closed solids, so it adds no mass; give it a mass override, or repair it into
  a closed solid` and `<part> has a solid with no volume (inside out or broken), so it adds no mass; ...` (warnings).
- `mass override of <part> must be a number of kg greater than 0, not <v>`.
- `contact mark of <part> must be "wheel" or "collide", not <v>`.
- `wheel friction of <part> must be a number greater than 0, not <v>`.
- `body '<b>' is marked as a wheel but doesn't turn on a Revolute joint (it is the grounded body | on <T> '<j>'); mark only wheels on an axle`.
- `wheel '<b>': its shape's center is <n> mm off its joint '<j>' line; the collision cylinder is centered on the joint axis` (warning).

**Slots**

- `<kind> slot name '<n>' must be letters, digits and _` / `slot name '<n>' is used twice; slot names must be unique`.
- `<kind> slot '<n>' is on joint '<j>', which closes a loop[; put it on tree joint '<t>']`: move the slot to a tree joint.
- `<kind> slot '<n>' is on <T> joint '<j>'; slots go on Revolute or Slider joints`.
- `<kind> slot '<n>': <key> must be a number greater than 0, not <v>` (gear ratio, efficiency, radius in mm).
- `motor slot '<n>': unknown motor '<m>'; known motors: Bag, CIM, Falcon500, ...` (the full list is in the message).
- `motor slot '<n>': count must be a whole number of motors (1 or more), not <v>`.
- `motor slots '<a>' and '<b>' both drive joint '<j>' with different gearing; motors on one joint share its gearbox, so give them the same gear ratio[ and radius]`.
- `encoder slot '<n>': on must be "joint" or "motor", not <v>`.
- `encoder slot '<n>' is on the motor shaft of joint '<j>', which has no motor slot; add one or put the encoder on the joint`.
- `<kind> slot '<n>' was on joint '<j>', which no longer exists; dropped` / `... is on joint '<j>', which is suppressed; dropped` (warnings).
- `gyro slot '<n>' was on component '<c>', which no longer exists; dropped` (warning).

**Mounts**

- `mount name '<n>' must be letters, digits and _` / `mount name '<n>' is used twice; mount names must be unique`: rename the
  mount (or the `mount_<name>` LCS).
- `mount '<n>' has a damaged frame; pick it again`.
- `mount '<n>' was on component '<c>', which no longer exists; dropped` (warning).
- Picking, in the Mounts section: `pick a flat face, a round edge or a cylindrical face of a part in the Assembly (got
  <what>)`. Straight edges, vertices, curved faces other than cylinders, and whole components are refused. Other
  messages: `the selection (<obj>.<sub>) isn't part of the Assembly '<name>'; pick ...`, `... (got the whole of
  <part>)` and `'<part>' isn't exported as a part of the Assembly (it is hidden and unjointed, or not a solid part)`.

**Folder, file and settings**

- `the export folder <path> exists and isn't a part folder (it has no part.toml); pick an empty or new folder`.
- `no such FreeCAD file: <path>` (command line).
- `<file> links to parts in other FreeCAD files that weren't found (<links>): open it in the FreeCAD panel, or keep the
  linked files next to it` (command line): the documents its App::Links point into aren't where the links say.
- `couldn't write the part folder <path> (<why>): close anything using <path> (Explorer, an editor) and export again`:
  the OS refused to swap the new folder in (on Windows, a folder that is open elsewhere); the old one is left as it was.
- `the saved scrimmage settings (ScrimmagePart) are damaged (<why>); delete the ScrimmagePart object and set the part up again`,
  `... aren't a JSON object`, `... have a damaged '<list>' list` / `'<table>' table`: the hidden `ScrimmagePart` object
  in the document was edited by hand. It is hidden in the tree; delete it from the Python console
  (`App.ActiveDocument.removeObject("ScrimmagePart")`) and set the slots up again.
- `the scrimmage settings were saved by a newer add-on (format <n>); update the scrimmage add-on`: run
  `scrimmage freecad install` from a newer checkout.
- `<what> was on component '<c>', which no longer exists; dropped` for mass overrides, contact marks, friction values
  (warnings): harmless; the panel removes them on the next save.

**FreeCAD itself**

- `the scrimmage add-on needs FreeCAD 1.1 or newer (this is <version>)`: upgrade FreeCAD.
- `FreeCAD not found: install FreeCAD 1.1+ (https://www.freecad.org/downloads.php, or `snap install freecad` on Linux) or set SCRIMMAGE_FREECAD_CMD to its freecad.cmd/FreeCADCmd`.
- `SCRIMMAGE_FREECAD_CMD=<x> isn't an executable`: the variable is set but isn't a path or a command on `PATH`.
- `the FreeCAD add-on ships with the scrimmage git checkout; install scrimmage with pip install -e <checkout>`:
  you installed Scrimmage from a wheel.
- Panel dialogs: `Open a document with an Assembly first.` and `Another task is open in the Tasks panel; close it
  first (OK or Cancel there).`
- Report view: `scrimmage: settings changed outside the panel (undo/redo?); re-open the scrimmage panel`.

## Tests

`pytest -m freecad` runs the exporter tests in headless FreeCAD (about 10 seconds once FreeCAD is found). They are
skipped when `SCRIMMAGE_SKIP_FREECAD=1` or no FreeCAD binary is found (`SCRIMMAGE_FREECAD_CMD`, then `freecad.cmd`,
`FreeCADCmd`, `freecadcmd` on `PATH`). The `freecad_gui` tests open a real FreeCAD window and run only with
`SCRIMMAGE_FREECAD_GUI=1`. Fixture Assemblies are built at test time by `tests/freecad/make_fixtures.py`. In CI the
`freecad` job downloads the pinned FreeCAD AppImage, extracts it (`--appimage-extract`) and points
`SCRIMMAGE_FREECAD_CMD` at the extracted `usr/bin/freecadcmd`.

## Panel checklist

A manual pass over the Scrimmage task panel in the FreeCAD GUI. `tests/freecad/gui_smoke.py` automates the same
steps (run it with `SCRIMMAGE_FREECAD_GUI=1 .venv/bin/python -m pytest tests/test_freecad_panel.py -k gui_smoke`; it
opens a FreeCAD window for a few seconds, and `SCRIMMAGE_GUI_PNG=/path/shot.png` keeps its screenshots). Do the steps by
hand after changing the panel, and whenever FreeCAD is upgraded.

1. **Open the `arm` fixture.** Build it with `tests/freecad/make_fixtures.py` (`build("arm", path)`, for example from
   FreeCAD's Python console) or open any saved Assembly. Switch to the **Scrimmage** workbench and click
   **Scrimmage part**. The Tasks tab shows five sections: Bodies, Joints, Gyro, Mounts, Export.
   - Bodies lists `Base` (0.216 kg) and `Arm` (0.324 kg); selecting a body row selects its parts in the 3D view.
   - Joints lists `Hinge`, Revolute, role `tree`, limits -30° … 120°.
   - Mounts lists `top` from the `mount_top` coordinate system.
2. **Add a motor.** Joints → Add motor; rename the slot `pivot`, choose a motor, set the gear ratio to 50. The list
   shows `pivot — <motor> on Hinge`. The spool radius stays grayed out (it applies only to Slider joints).
3. **Add a mount by picking.** Click the arm's top face in the 3D view, then Mounts → Add from selection. A mount
   `Arm_mount` appears at `(150, 10, 20) mm, z axis (0, 0, 1)` in the arm's frame. Try a straight edge or a vertex:
   the section explains what to pick instead. A circular edge or a cylindrical face gives its center and axis; such an
   axis has no inside or outside, so it points along whichever of the part's own x, y or z it is closest to, positive
   side (rotate the mount in the robot config if you need the other way).
4. **Override a mass.** Bodies → the `Arm` part's Override kg: 2.5. The `Arm` body's mass changes to 2.500; set it
   back to — to use the computed mass again. Mark a part `wheel` and check its Friction box becomes editable. Each
   change is one Edit → Undo step. An undo or redo of a settings change closes the panel (the report view says
   "settings changed outside the panel"); re-open it to see the restored settings. It never overwrites them.
5. **Export.** Export → choose a new or empty folder (or one holding an earlier export) and click Export. The box
   shows the summary: bodies `Base, Arm`, joint `Hinge`, slot `pivot`, mounts `top, Arm_mount`, total mass
   2.716 kg. Export into a folder that holds other files: the box lists the problem and nothing is written.
6. **Re-open.** Close the panel, save the document, close it and open it again. Open the panel: the slot, the mount,
   the override and the export folder are all still there. Close the document while the panel is open: the panel
   closes with it.
7. **Re-export.** Click Export again: the same summary, and the folder is replaced. Close the panel, delete the `Hinge`
   joint, open the panel and export again: the summary warns that slot `pivot` was on a joint that no longer exists
   and drops it (the panel reads the Assembly when it opens, so re-open it after editing the Assembly).
