# The Part tab

The Part tab turns a CAD export (a STEP file, or STL/OBJ files) into [part folders](parts.md) you can put in a robot,
without leaving the browser. You say which pieces move together, what they are made of, and which ones turn or slide.
Scrimmage works out the weights and the center of gravity (CG) and writes the files. It is the same page as the
[assembler](assembler.md) (`scrimmage edit`), with the same habits: every change is saved at once, there is no Save
button, and Undo steps back.

Contents: [Install](#install), [Starting it](#starting-it), [The three columns](#the-three-columns),
[Where's the robot?](#wheres-the-robot), [Bodies](#bodies), [Materials and weight](#materials-and-weight),
[Joints](#joints), [Motors and sensors](#motors-and-sensors), [Mounts](#mounts), [Touching things](#touching-things),
[The checklist](#the-checklist), [Importing again](#importing-again), [Parts made by a CAD plugin](#parts-made-by-a-cad-plugin),
[Using the parts in a robot](#using-the-parts-in-a-robot), [Troubleshooting](#troubleshooting).

## Install

Reading CAD files needs two extra Python packages. In the `scrimmage` folder, with the venv on:

```bash
pip install -e ".[cad]"
```

(`[cad]` adds `cascadio`, which reads STEP, and `trimesh`, which reads STL and OBJ.) Without it the tab opens but
says `Reading CAD files needs the cad extra: pip install "scrimmage[cad]"`.

## Starting it

Export your robot (or one mechanism) from your CAD program. In Inventor, save a copy as a **STEP** file (`.stp` or
`.step`) from an assembly; each part in the assembly becomes a piece. Then:

```bash
scrimmage edit my_robot.step                      # a STEP file (or .stp)
scrimmage edit my_robot.step --robot my_robot.toml  # same, and the parts go in parts/ next to that robot config
scrimmage edit parts/my_robot.import.toml         # an import you made before
scrimmage edit parts/arm                          # a part folder made by a CAD plugin (see below)
```

You can also start from the start page (`scrimmage edit` with no file): **New part from CAD** lets you **Browse…**
for the file, drop one on the page, or press **Choose a file…**. For a folder of STL files, Browse to the folder and
press **Use its STL files** (one piece per file). In the assembler, **Import CAD…** in the parts library does the same for the robot you
have open.

The first time, Scrimmage reads the file, guesses the bodies, and writes `<name>.import.toml` into the `parts/` folder
next to your robot config (or next to the CAD file). That file holds every choice you make, so the CAD file itself is
never changed. A file you drop on the page is copied next to it, in `<name>.source/`; a file you Browse to is used
where it is.

One import can make several parts. A climber with a base and a swinging arm can be two parts, each in its own folder
under `parts/`. See [Bodies](#bodies).

## The three columns

- **Left: Bodies (pieces that move together) and Still needed.** The tree shows each body with its pieces under it.
  Drag a piece onto another body to move it there. Bolts, nuts and other hardware are folded into the body they belong
  to and show as "+N hardware". **New body from selection** and **Leave out** act on the pieces you selected (Shift-click
  adds more). Below the tree is the [checklist](#the-checklist).
- **Middle: the 3D view.** Drag to orbit, right-drag to pan, scroll to zoom. **Fit to view** fits the part in the
  window. **Show left out** draws the pieces you left out, faintly. Click a piece to select it. Before you have checked
  the robot frame, the [Where's the robot?](#wheres-the-robot) step sits over the view.
- **Right: the Inspector.** It shows what you can change for the selected piece or body: material, weight, joint,
  motor, mounts and so on.

The bottom bar shows whether your changes are saved, **Parts up to date** (or **Updating parts…** while the part
folders are rebuilt in the background), the weight, and **Import again**. You can keep working while it rebuilds.

## Where's the robot?

Every CAD program picks its own idea of "up". Inventor's default is Y up, many others use Z up. Scrimmage needs to know
which way is up and which way is the front, so the robot stands on the floor and drives forward. Until you say it is
right, a **Where's the robot?** box stays over the 3D view. It shows a floor grid and a red arrow for the front.

- **Which way is up.** Press **Y is up** (Inventor saves this way) or **Z is up**. If the robot lies on its side, try the
  other one.
- **Turn front 90°** turns the front a quarter turn, seen from above. Keep pressing it until the red arrow points where
  the front of your robot is.
- **Click the floor** lets you click a flat face the robot stands on, such as the bottom of a wheel. **Click the
  frame's center** lets you click the middle of the robot, seen from above. Both are optional. Without them the floor
  is the bottom of the robot and the center is the middle of its bounding box.

When it looks right, press **Looks right**. If the file is a mechanism and not a whole robot (an arm or a climber on its
own), press **This isn't a whole robot** and the CAD file's own axes are used as they are.

The box stays open until you press one of those two buttons. Changing the frame later (in the Inspector, **Robot
frame**, **Check up and front**) opens it again, because a new frame needs a new look. If it says "This is a guess:
most CAD programs use +z up, but Inventor uses +y up", it is waiting for you.

## Bodies

A **body** is a group of pieces that move together. A wheel and its hub are one body. A frame with its bolts is one
body. Scrimmage starts by guessing: sub-assemblies in the STEP file become bodies, and bolts, nuts and washers fold
into the body they sit in. A hardware piece is never left out by itself; only you can leave a piece out.

- **Move a piece:** drag it from the list onto another body, or select it and pick a body in **In body**.
- **Make a new body:** select pieces and press **New body from selection**.
- **Leave out:** select pieces and press **Leave out** (**Put back** brings them back). Left-out pieces weigh nothing
  and are not drawn, unless you turn on **Show left out**.
- **Its own part:** in the Inspector, a body can be **Its own part**, so it is placed on the robot by itself. Every
  import makes at least one part. The **Part name** is its folder name in `parts/`. The other choice, **It hangs on**,
  makes a body move with another body instead.

## Materials and weight

Click a piece (Shift-click for several) and pick its **Material**: aluminum 6061, steel, polycarbonate, HDPE, plywood,
carbon fiber, nylon, PLA or PETG. For 3D printed ones, set **Infill (percent)**; empty means 40%. **Something else**
lets you type a density in kilograms per cubic meter (aluminum is 2700, steel 7850).

A piece with no material chosen yet is built as aluminum and listed on the [checklist](#the-checklist) until you pick
one. Picking aluminum on purpose settles it.

Weight comes from the shape of each piece times its density. If you know the real weight, type it: in the Inspector,
**Weight** on a piece or on a body wins over the material. Empty the box to go back to the material. The Inspector
shows the part's weight and center of gravity ("Weight 12.4 kg, center of gravity ..."), and the bottom bar shows the
total.

Open shapes (a sheet-metal export that is a surface, with no inside) have no volume. They show up on the checklist
as "no volume": type a weight for them or leave them out. A body that ends up with no weight at all is built with a
0.001 kg placeholder and a checklist item, so the part still loads.

## Joints

A new body is **No (welded)**: it moves only when the body it hangs on moves. In the Inspector, **How it moves**
(the **Moves** choice) has **Turns** (a hinge, like an arm or a wheel) and **Slides** (like an elevator). Then give
it an axis:

- **Pick the axis** then click, in the 3D view, a hole or a round face (a bearing or a shaft) for something that turns,
  or a flat face that faces the way it slides.
- Or **Snap to** the robot's X, Y or Z.

**A turns or slides joint is not saved until its axis is picked or snapped.** Until then the Inspector says "Not saved
yet", and Esc cancels. This is so Scrimmage never guesses an axis for you.

**More: range, spring, damping, friction** has the range (in degrees for a turn, meters for a slide, measured from
where the CAD shows the part), a spring and its rest position, damping, and friction. Leave the range empty for no
limit. If you type one end of a range, type the other end too.

**Try it** gives you a slider that moves the drawing in the 3D view, so you can check the axis is right. It only moves
the picture. Nothing is saved.

Two more joint kinds are in **More: turns with another joint, pinned to another body**: **Turns with another joint**
(gears or a belt: the two move together) and **Pinned to another body** (for a linkage that closes on itself, like a
four-bar).

## Motors and sensors

Once a body has a joint, **Motor and sensors** appears. Pick a **Motor** type, then **How many** motors, the **Gear
ratio** (motor turns per turn of the joint), and, for a rope or belt that winds on a drum, the **Spool diameter**. The
motor is not saved until the spool diameter is typed, and the Inspector says so. An **Encoder** is where the robot code
reads how far the joint has moved, **On the joint** or **On the motor**. A body can also have a gyro (like a navX or
a Pigeon).

A motor or encoder needs a joint, so make the body turn or slide first.

## Mounts

A **mount** is a spot where another part, or the robot, bolts on. Press **Add mount**, name it (such as `top`), and
click a flat face in the 3D view where the other part attaches. Other parts then attach to it by that name. Remove a
mount with its remove button.

## Touching things

**Touching things** says how a body bumps into game pieces and the field: **Only shows (doesn't touch anything)**,
**A wheel**, **Touches things, as shaped**, or **Touches things, as a simple box**. Hooks and claws that grab need to
touch things as shaped. A wheel needs a joint that turns. When a body looks like a wheel or a box, Scrimmage offers
the matching choice with a "Yes" button.

## The checklist

**Still needed** lists what is left, and clicking an item selects what it is about. The mark tells you how serious it
is: **To do** is work left, **Check** is worth a look, **Problem** means the part will be wrong. Typical items:

- pieces still guessed as aluminum (no material chosen yet);
- pieces with no volume;
- pieces matched by shape after importing again: check them;
- welded bodies that probably move: give them a joint if they do;
- a body with a joint but no motor;
- the robot frame, until you press **Looks right** or **This isn't a whole robot**.

When it says "Nothing left to do." you are done. Items about choices you made on purpose (like leaving a piece out)
are never listed.

## Importing again

You changed the CAD? Export it again over the same file, then press **Import again** (or drop the new file on the
page to use it in place of the old one). Scrimmage keeps your settings: it matches each piece by its **name** first,
then by its **shape**. A piece matched only by shape goes on the checklist so you can check it. New pieces get
guessed bodies. A piece that is gone is dropped, and a joint or mount that belonged only to it is dropped with a
message saying "no longer in the CAD". Nothing else is lost. Your choices are stored in CAD coordinates, so
re-importing does not move your axes.

## Parts made by a CAD plugin

The FreeCAD add-on ([freecad.md](freecad.md)) writes part folders with their own `part.toml`. Open one with
`scrimmage edit parts/arm`. The tab still shows the bodies, but there are no pieces to move, the frame is whatever the
plugin wrote, and the joints, slots and axes come from the plugin and cannot be changed here. What you can change is
the weight, the joint's range, spring, damping and friction, how it touches things, and mounts. Those edits go in
`scrimmage-edits.toml` inside the part folder, never in `part.toml`. The FreeCAD add-on keeps that file when you export
again. If an edit is about a body that has since gone from the CAD, you get a warning and the edit is ignored.

A known limit: parts made by a plugin get no **Try it** slider.

## Using the parts in a robot

Open your robot config in the assembler (`scrimmage edit my_robot.toml`). The parts folders show under **Part folders**
in the library and can be added like any other part. For a part made from a CAD file:

- **Import CAD…** in the library opens the Part tab for a new file. When you are done, press **Back to the robot** at the
  top of the page. (It is only there for the robot you came from.)
- **Placement:** the part is put where the CAD has it. Its **Attach to** is preselected to what the CAD hangs it on:
  the chassis for the base, or a body of another part from the same import for an arm on a base. The Inspector says
  "Placed where the CAD has it". If you move it, it says "Moved from where the CAD has it", and **Reset to CAD** puts
  it back.
- **CG:** the bottom bar shows the robot's whole center of gravity ("CG: 0.31 m up, 0.02 m forward"), and the **CG**
  button over the 3D view shows it as an orange ball with a line down to the floor.

## Troubleshooting

- **"Reading CAD files needs the cad extra".** Run `pip install -e ".[cad]"`, then press **Import again**.
- **The robot lies on its side, or the CG height looks wrong.** The up direction is wrong. Open the Inspector's
  **Robot frame**, press **Check up and front**, and press **Y is up** or **Z is up**.
- **The robot faces the wrong way.** Press **Turn front 90°** in the same step.
- **A piece is missing from the 3D view.** It may be left out (turn on **Show left out**), or the CAD file has an open
  surface instead of a solid. Check the list for "no volume".
- **My arm weighs nothing / the total is too small.** Open shapes have no volume. Type a weight for the body.
- **"That click didn't work".** The click missed the part or hit a flat face when an axis needs a round one. Click
  again on a hole or a round face.
- **The 3D view says WebGL isn't available.** The list and the Inspector still work. Reload, or turn on hardware
  acceleration in the browser.
- **"Parts not updated".** The background rebuild failed; the message says why. Fix it and edit again.
- **A plugin-made part opens read-only.** Its `part.toml` has an error: export it again from the plugin.
