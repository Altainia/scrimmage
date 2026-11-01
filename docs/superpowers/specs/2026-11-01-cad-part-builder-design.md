# CAD to part, in the browser: design

Sub-project 18. Branch `cad-part-builder`, from `main`.

## Why

A CAD student finishes a mechanism, or a whole robot, and wants to see it work in Scrimmage: does the climber
reach the rung, does the robot balance, will it tip over on a bump. Today the only way a CAD model becomes a moving,
weighed Scrimmage part is the FreeCAD add-on. The team designs in Autodesk Inventor, and other teams use Onshape,
SolidWorks, Fusion and others. The owner plans an Inventor plugin, but plugins are a weak foundation:
- Each CAD program needs its own plugin.
- Inventor licenses (needed to keep a plugin working across Inventor updates) are expensive and monthly.

So the browser becomes the main way in, and any CAD plugin is an optional shortcut. **Anything a CAD plugin can
write, a student can set in the browser instead.** Any team, with any CAD program that exports STEP or STL, can
build a part without a plugin, and a plugin's output can be finished or changed in the same place.

Mass and center of gravity matter most. The owner's main uses are testing balancing and climbing mechanisms and
strategies, and making sure the robot is unlikely to tip over on a bump. All of these depend on where the weight is,
so the material (density) of each piece must be settable and must drive mass, center of gravity and inertia.

This is the first of two projects agreed on 2026-11-01:
1. **This one:** CAD to part.
2. **Next:** testing a component without code. A stand-in robot (a box with mass and center of gravity), Scratch-style
   blocks that move the motors ("move arm A to 0.85 m", "wait until arm B's hook touches a rung", "repeat"), saved as
   the same text scripts `scrimmage climb run` reads, with runs, sweeps and animations on the Bench-tab model. The
   owner's example: a student lowering a ladder climber (two oscillating arms with spring-hinged hooks) tries
   different placements and arm lengths on a stand-in box, watching it climb and swing.

Driving a whole robot by hand for a match and recording it as a scenario already exists (the Drive tab). That is for
robots with real robot code; it isn't part of either project.

## Who

- **CAD students**, the main users: high schoolers who are technically minded but still being trained. They are on
  Windows, mostly using Inventor. They can export a whole assembly or one component at a time, whichever is asked.
- Students on other teams with other CAD programs.
- Programmers and mentors, who later bind device IDs and run robot code on the result (existing tools).

## The anti-goal that rules the rest

**Unnecessary friction** (the owner, 2026-11-01): "The more annoying a tool is to use, the less it'll be used." Every
screen is judged by how few steps and how little prior knowledge a high schooler needs. Concretely:
- The first look is immediate: drop the file in, and the model shows up in 3D, in the right place, at the right size.
- Every setting has a sensible default, so the part is usable (it loads, weighs something plausible, shows up in a
  robot) before the student sets anything. What is still a guess is listed in plain words, not hidden.
- Plain words over jargon: "pieces that move together", not "rigid bodies"; "turns" / "slides", not "revolute" /
  "prismatic". The exact terms can appear in small print for people who know them.
- Clicking in 3D over typing numbers. Numbers stay available for those who want them.
- Changing the CAD and importing again keeps everything the student set (the tweak-and-retest loop is the point).
- Problems say what's wrong and what to do, in one sentence, next to the thing that's wrong.

## Goals

1. **Import.** In the existing local web app (`scrimmage edit`), a new **Part** tab.
   - **STEP files** (`.step`, `.stp`): a whole assembly or one component. Names, nesting and positions come along,
     so pieces arrive named and in place.
   - **STL and OBJ files**: one or several (a folder or a multi-select). Each file is a piece named after the file.
     Units are guessed from the size (a 300-unit-long piece is millimeters, 0.3 is meters, 12 is inches) and shown as
     "looks like millimeters", with a change.
   - **An existing part folder** (one written by the FreeCAD add-on, a future Inventor plugin, or this tab). See goal 7.
   - Drag-and-drop onto the page, or Browse. Starting from the start page ("New part from CAD") and from the
     assembler's Parts library ("Import CAD...") both work.
   - Big assemblies stay usable. A whole-robot STEP has hundreds of pieces (every bolt), so it must load in seconds
     and stay smooth to orbit, and fasteners must be easy to deal with (goal 2).
2. **Pieces that move together (bodies).** The tree on the left lists the pieces. By default, STEP's own nesting gives
   the grouping: each top-level component is one body, so a sub-assembly (a gearbox, a hook assembly) moves as one.
   The student can:
   - drag pieces between bodies, merge bodies, or split a sub-assembly;
   - rename a body;
   - **leave a piece out** (it neither shows nor weighs), for example a drivetrain the student will use a stock part
     for instead.
   Small hardware (bolts, nuts, washers, rivets, recognized by name or by being tiny) joins whatever body it touches
   or sits inside, without asking.
3. **Weight: materials and mass.**
   - Each piece has a material from a short list of FRC materials, with densities: aluminum (6061), steel,
     polycarbonate, HDPE, plywood, carbon fiber, nylon, a "3D printed" entry with an infill percentage, and custom
     (type a density). The default is aluminum, the same as the FreeCAD exporter.
   - Or the student types a **weight** for a piece or a whole body (for a motor, a battery, or anything bought).
   - Material can be set on many pieces at once (select several, or a whole body or sub-assembly).
   - Scrimmage computes each body's mass, center of gravity and inertia from mesh volume and density. The 3D view
     shows each body's center of gravity and the whole part's.
   - **Scale to a known total.** If the student knows what the real thing weighs (they put it on a scale, or the
     robot is at its weight limit), they type it, and Scrimmage scales the masses it computed to match. Typed
     weights stay as typed. This keeps the center of gravity where the CAD puts it while fixing the total.
   - A piece whose volume can't be computed (an open surface, a sheet-metal skin) is listed with what to do: type a
     weight, or leave it out.
4. **Joints: what moves, and how.**
   - The student picks a body and says how it moves relative to its parent: **turns** (a hinge) or **slides**.
   - The axis is set by clicking in 3D: a hole or a round face for a hinge (Scrimmage fits the axis), or an edge or a
     flat face for a slide. Two clicked points, or snapping to x/y/z, are the fallback.
   - Travel limits, a spring (stiffness and rest position), damping and friction, all optional.
   - **Try it:** a slider moves each joint through its travel in the 3D view, so the student sees at once whether the
     axis, direction and limits are right before running anything.
   - Linked joints (gears, belts, chains, rack and pinion: "this joint turns 3 times for each turn of that one") and
     closed loops (four-bars) map to the part format's existing `[[coupling]]` and `[[loop]]`.
5. **Motors, sensors and mounts.**
   - A motor on a joint: motor type from Scrimmage's list (Kraken X60, NEO, CIM and so on), how many, gear ratio,
     and for a slide the spool or pulley diameter (in inches or mm, as the student likes). Optional: inverted,
     efficiency.
   - An encoder on a joint or on the motor shaft, and a gyro on a body.
   - Slot names default to something readable ("arm_A motor" becomes `arm_A_motor`). The programmer binds device IDs
     later in the assembler, as today.
   - **Mounts:** where other parts attach, set by clicking a face or a hole.
6. **What touches things (collision).** By default, meshes only show; they don't collide, as with the FreeCAD
   exporter. Per body, the student picks:
   - **wheel**: a collision cylinder about its joint;
   - **touches things, as shaped**: the mesh collides, split into convex pieces when it is concave. This reuses the
     bench's `coacd` splitting (`scrimmage/bench/cad.py`), so a J-hook catches a rung by its real shape.
   - **simple shape**: a box or capsule fitted to the body, cheaper and steadier.
   The default suggestion for a body named like a hook or a wheel is shown but not applied silently.
7. **Changing the CAD and importing again keeps the student's work.**
   - Every setting is stored keyed by the CAD's own piece and sub-assembly names, so re-importing a changed STEP (or
     changed STLs) applies them again.
   - A setting whose piece is gone is listed ("hook_B is no longer in the CAD; its material was dropped"). New pieces
     get the defaults and are listed as new.
   - **Plugin output is a source too.** A part folder written by the FreeCAD add-on (or a future Inventor plugin) opens
     here, and the student's changes in the browser are kept on top. A later re-export from the plugin doesn't erase
     them. The page shows which values come from the plugin and which were changed here.
8. **What's left: a checklist.** Like the Robot code panel's readiness list, one panel lists what is still a guess
   or missing, each with a "fix it" link that selects the thing in 3D:
   - pieces still on the default material;
   - bodies with no joint (they will be welded to their parent, which is fine for a frame, but listed);
   - joints with no motor;
   - concave collision meshes without `coacd` installed.
   A part never needs an empty checklist to be saved or used.
9. **The result is an ordinary part folder.** Saving writes `part.toml` plus meshes, exactly the format in
   `docs/parts.md`, so everything downstream (the assembler, scenarios, the bench, agents, the CLI) works unchanged.
   Parts are saved where the assembler already looks: `parts/<name>/` next to the robot config, or the team's
   `{cad}` folder.
10. **Parts that came from the same CAD put themselves together.** When parts are imported from one robot assembly
    (or from components exported in the same coordinates), adding them to a robot in the assembler places them where
    the CAD has them relative to each other. The student only picks what they attach to, not the offsets.
11. **Whole-robot weight in the assembler.** The assembler's bottom bar already shows total mass. It also shows the
    robot's center of gravity (height above the floor, and where it sits on the frame), and the 3D view can mark it.
    This is the number that decides tipping, and it is what the CAD material work is for.

## Non-goals

- The Inventor plugin itself, and any other CAD plugin. The owner may write one; it writes a part folder, which goal 7
  covers.
- Testing a component without code: stand-in robot, blocks, sweeps (project 2).
- Detecting joints automatically from the CAD. STEP files almost never carry joints. A guess that is wrong is worse
  than asking.
- A catalog of bought parts (motors, gearboxes, swerve modules) with known weights. Typed weights and "scale to a
  known total" cover them for now.
- Reading materials or colors out of STEP files. Most exports don't carry density reliably. If a file does name a
  material that matches the list, using it is welcome but not required.
- Editing geometry: no moving holes or changing lengths here. Geometry changes happen in CAD, then the student
  re-imports.
- Native CAD formats (`.ipt`, `.iam`, `.sldprt`, Onshape documents) without exporting first.
- Replacing the FreeCAD add-on. It keeps working, and its output opens here.

## Anti-goals

- Anything that makes a high schooler stop and look something up to get the first picture on screen.
- Losing a student's settings when the CAD changes.
- Silently wrong weight. A plausible-looking robot whose center of gravity is far off is worse than one that says
  "these 40 pieces are still guessed as aluminum".
- A second file format. The result is a plain part folder; the student's import settings live beside it.

## Constraints

- Lives in the existing `scrimmage edit` web app (`scrimmage/assembler/`), its token-gated local server, the vendored
  three.js, its dark theme and its all-screen contrast test.
- Students are on **Windows**. Everything here must work there. The project is pure Python plus the browser, and
  every dependency it adds must have Windows wheels.
- Python 3.11 and newer (CI tests 3.11 and 3.13).
- The part format (`docs/parts.md`) is the contract. If the format must grow (goal 10's CAD frame, say), it grows the
  documented way: new optional keys, with `format` raised only if old Scrimmage can't safely ignore them.
- Units in files stay SI: meters, kilograms, degrees.
- American spellings in code and docs.

## Decided with the owner

- The browser is the main way to turn CAD into a part. Plugins are an optional shortcut, so other teams and other
  CAD programs work, and keeping up with Inventor updates matters less.
- STL (or similar mesh) export must be enough. The browser can set anything a plugin could export: material and
  density (which drive mass and center of gravity), bodies, joints, motors, how things connect.
- When the export has the data, parts put themselves together. When it doesn't, the browser is where the student
  does it. Either way, the browser can change it afterwards.
- Students will export whole assemblies or single components, whichever is asked.
- Low friction for high schoolers is the top priority.
- Two projects, this one first. Blocks (for project 2) were picked over a timeline or recording for driving a
  component without code. Hand-driven recordings are for whole-robot match scenarios with real robot code.

## My calls (the owner may override)

- **STEP as well as STL.** Every CAD program exports STEP, and unlike STL it keeps names, nesting and positions. That
  is what lets pieces arrive in place without a plugin.
- **STEP reader: `cascadio`** (MIT, built on OpenCascade, the engine FreeCAD uses), with `trimesh` for mesh work. It
  has wheels for Windows, macOS and Linux on Python 3.11 and up. A spike on 2026-11-01 converted a nested FreeCAD STEP
  assembly to glTF: product names (including a nested sub-assembly) and positions came through, converted to meters,
  and volumes were exact for boxes and within 1% for a tessellated cylinder. The converted meshes aren't watertight
  until duplicate vertices are merged (glTF splits vertices per face). Both are new dependencies, in an optional extra
  (`pip install scrimmage[cad]`), with the Part tab saying how to install it if missing. Nothing else in Scrimmage
  needs them.
- **New tab in `scrimmage edit`**, not a separate app. Students already open that page for the robot and the bench.
- **Settings stored beside the part, keyed by CAD names.** A small import-settings file in the part folder holds the
  student's choices (bodies, materials, joints, slots, mounts, collision) and the source file's path. `part.toml` and
  the meshes are generated from source plus settings. This is how re-import (goal 7) and plugin layering work, and
  it matches how the FreeCAD add-on keeps its settings inside the `.FCStd`.
- **Reuse the FreeCAD exporter's body-tree logic.** `freecad/Scrimmage/scrimmage_part/tree.py` (spanning tree,
  loops, couplings, saved-pose limits) is plain Python with no FreeCAD dependency. Move the shared core into
  `scrimmage/parts/`, with the add-on keeping a synced copy as `freecad/sync.py` already does for `toml_writer.py`.
  Mass properties come from the meshes (`trimesh`) instead of FreeCAD's solids.
- **Default material aluminum**, as the FreeCAD exporter does, but every default-material piece stays on the
  checklist.
- **"3D printed" density** is the plastic's density times (shell share + infill share). The builder picks a simple,
  documented approximation; the student can always type a weight.
- **Hardware auto-merge** (goal 2) by name (bolt, screw, nut, washer, rivet, standoff, spacer, and common
  McMaster-style part numbers) or by size (under ~1% of the assembly's size). It never leaves pieces out on its own.
- **One import can make several parts.** A whole-robot STEP becomes one part by default. In the tree, the student can
  mark a sub-assembly "its own part" (the climber, the elevator), and one import then saves several part folders.
  They share one import-settings file and one CAD frame, and a re-import updates all of them. Importing the same
  STEP several times would also work, but it is more friction, so it isn't the intended way.
- **Lining up with a stock drivetrain: one "where's the robot?" step per import.** A CAD origin can be anywhere, and
  the stock chassis's origin is the frame center on the floor. So each import has a robot frame:
  - **floor:** by default, the lowest point of the assembly;
  - **center:** by default, the center of the footprint;
  - **front:** by default, +x.
  It's shown in 3D as a floor grid and a "front" arrow for the student to confirm or fix by clicking: the floor, the
  frame's center, and which way is front. A student importing a lone component that isn't in robot coordinates
  skips it and places the part in the assembler as today. Parts from that import attach to the stock chassis at the
  offset this frame implies, which is what makes goal 11's center of gravity right.
- **Plugin parts are edited one body at a time.** A plugin's part folder has already merged pieces into bodies and
  baked in masses (often better masses: CAD's own). In the browser, the student can change a body's weight (or
  scale to a known total), joints, slots, mounts and collision, but not per-piece materials. Per-piece work means
  importing the STEP instead.
- **The FreeCAD add-on changes so a re-export keeps the browser's settings.** Its folder swap keeps the
  import-settings file, and the browser applies those settings on top of the new export. That's the contract for any
  plugin, the future Inventor one included: write the part folder, and leave the settings file alone.
- **Whole-robot center of gravity in the assembler** (goal 11) is included because it is cheap and is the reason for
  material work. A deeper tipping analysis waits for project 2.
- **Windows CI.** The repository is public, so CI minutes aren't limited (owner, 2026-11-01). Add a Windows job that
  runs this project's import and part-writing tests (no robot program, no rendering), since students are on Windows
  and Windows is otherwise untested (usability-future-work item 3).
- **Re-import matching doesn't trust names alone.** No Inventor STEP export is available yet (the owner expects to
  send one, plus a re-export after a small change, later in the week of 2026-11-01). So settings match pieces by
  their full path in the assembly first, then by a geometry fingerprint (volume, size, shape) and position, so
  renamed or moved pieces and repeated identical pieces (two hooks, forty bolts) still find their settings. When a
  match is a guess, the page says so ("hook_B: matched by shape; check it"). The Inventor files, when they come, are
  added as test fixtures, and the rule is tuned to them.
- **Test fixtures:** small STEP files made once with FreeCAD (installed here as a snap) and committed, plus STLs. Real
  Inventor STEP exports are welcome from the owner later as extra fixtures.

## Left to the builder

- The import-settings file's name and layout, and how plugin output is layered under browser changes.
- How a hole or round face becomes an axis on a mesh (cylinder fit to the clicked triangles, circle fit to an edge
  loop, or similar), and how slide axes are picked. The requirement is a couple of clicks for common cases.
- How goal 10 records CAD coordinates (a part-level frame key, or offsets computed when parts are added), within the
  part format's growth rules.
- How large assemblies are kept fast: mesh decimation for display, instancing repeated hardware, lazy loading.
- The exact material list and densities, and hardware-name patterns.
- Screen layout within the app's existing three-column pattern (tree / 3D / inspector), following the mockups shown
  on 2026-11-01 (`part-builder.html`): a tree of bodies and pieces, a checklist of what's left, the 3D view with
  clickable axes and centers of gravity, and an inspector for material, joint, slot and mount.
- Docs: a `docs/part-tab.md` page in the style of `docs/assembler.md`, a getting-started guide step ("bring your CAD
  in"), and README updates.

## Decisions made during the build

- The part builder does not reuse the FreeCAD exporter's `tree.build()`, because FreeCAD joints form an undirected graph that needs a spanning tree, while here the student names each body's parent directly (loops and couplings are explicit entries), so only `sanitize` and the `principal`/`rpy_deg` math are shared, the math ported to numpy in `scrimmage/cad/mass.py` and tested against the add-on's copy.
- The student's choices live in `<name>.import.toml` beside the part folders, with a top-level `[import]` table that makes `classify()` return `"import"`, and geometric choices (joint anchors, axes, mount points) are stored in CAD coordinates so changing the robot frame or importing again does not invalidate them.
- Each part from an import is written to `parts/<part name>/` with its `part.toml`, meshes and an `import-origin.toml` marker (the import file, an import id, and each body's origin at zero pose), so the library still finds the folders and the assembler can place parts where the CAD has them.
- Every body frame is aligned with the robot frame and a child body's origin is its joint anchor, so a part attached to the stock chassis's root body at zero offset sits exactly where the CAD has it.
- A file dropped on the page is copied to `parts/<import name>.source/` so the import never depends on a temporary upload, while a file picked with Browse is used in place (with `{cad}` when it is under the team folder), and Import again re-reads whatever path is stored.
- Edits to a part made by a CAD plugin are kept in `scrimmage-edits.toml` inside its folder and applied by `load_part`, so the plugin's `part.toml` is never changed and the FreeCAD add-on's folder swap can keep the edits when it exports again.
- The robot frame stores up and front as one of `+x -x +y -y +z -z`, guessing `+z` up and `+x` front, with a one-click "Y is up", because Inventor, which these students use, defaults to Y up.
- A 3D printed material weighs `density * (0.25 + 0.75 * infill)` (PLA 1240 and PETG 1270 kg/m^3, infill 40% unless changed), because a printed part is a solid shell of about a quarter of the plastic with infill for the rest.
- Part folders are rebuilt by one background worker per session where the latest edit wins, and the state reports idle, building or failed, so typing never waits on a rebuild.
- A re-imported piece that was renamed and moved (about 100 mm) is matched by shape alone and is listed as a guess to check, following the 5 mm rule in the plan, because the student should see guesses rather than have them hidden.
- Hardware inside a sub-assembly that also holds other pieces stays in that sub-assembly's body and only stand-alone hardware merges, because a screw belongs with the assembly it is in and the student can move it if not.
- When a body that had a joint is removed and its child becomes the root, the child's joint is cleared with a message, because a root body cannot have a joint.
- A part folder's `folder` and `source` paths resolve `{cad}` and `{code}` through the team folders first and otherwise relative to the import file's folder, so the same import file works on every laptop.
- `apply_edit` returns the new import file together with its list of messages, because the planned signature silently dropped "your joint was dropped" messages that the spec says to show.
- Folders from an earlier build are removed only if this import recorded writing them and their marker still names this import, because scanning siblings could delete a student's hand-made backup copy.
- The build worker saves the import file after writing the part folders, because the list of folders written is updated in place and stale folders would never be cleaned otherwise.
- A piece's material starts as "not chosen" (built as aluminum and listed on the checklist), and picking aluminum on purpose clears the item, because otherwise a robot that really is aluminum would be nagged forever.
- Notes found while loading a part with edits go into the part's warnings, which the assembler adds to the world's warnings, because Python's `warnings.warn` never reaches the browser or the scenario reports.
- The wheel and box fitting shared by the builder and the plugin overlay lives in numpy-only `scrimmage/cad/fit.py`, and `scrimmage/cad/__init__.py` imports neither trimesh nor cascadio, so loading a plugin part never pulls in the heavy CAD libraries.
- A Task 1 test imports the add-on's `mass.py` as a package, not by file path, because its relative import needs its package.
- `source.py` wraps the bench's mesh errors as `CadError` with the same text, so the whole cad package raises one kind of error that the page can show.
- The same "body emptied" rule (remove the body, drop its joint, slots and mounts, re-parent its children, and say so) is one helper used by importing again and by moving pieces, so the two cannot disagree.
- A pure-Python test covers the add-on's keep-the-edits-file logic so the main CI job tests it, since the headless FreeCAD test only runs in the freecad job.
- The inspector sections live in their own file, `part_inspector.js`, with the tree's joint wording checked against the saved joint kinds, because `part.js` would otherwise pass 1,100 lines.
- A new turns or slides joint stays a draft in the browser until an axis is picked or snapped, because saving it earlier would mean a silently guessed axis, and a student who reloads mid-pick only repeats one click.
- Changing the robot frame does not confirm it, and only "Looks right" (or "This isn't a whole robot") does, because the frame step exists to make the student look, and any frame change reopens it.
- The second part of an import is preselected onto what the CAD hangs it on, recorded as an `attach` entry in `import-origin.toml` (an internal marker; `part.toml` is unchanged), because the old behavior fixed it to the chassis while claiming "where the CAD has it".
- Half-typed text in the inspector's boxes survives a redraw and is committed once, because students were silently losing the values they had typed.
