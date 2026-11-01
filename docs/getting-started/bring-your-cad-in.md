# Bring your CAD in

For CAD students and mentors who want to turn a robot or mechanism from their CAD program into something Scrimmage can
use: weights, a center of gravity, and joints that turn or slide. No programming.

[← All guides](README.md) · Needs: [the install](install.md), steps 1 to 3

## Your first ten minutes

**1. Add the CAD reader.** In the `scrimmage` folder, with the venv on:

```bash
pip install -e ".[cad]"
```

This adds the two small libraries that read CAD files. Without them, the page tells you the exact command to run.

**2. Save your CAD as a STEP file.** In Inventor, open the assembly and use **Save Copy As**, then choose **STEP**.
Other CAD programs have the same thing under Export. (A folder of STL files also works.) To try it first without
your own file, use the climber that ships with Scrimmage, `tests/data/cad/climber.step`.

**3. Open it.**

```bash
scrimmage edit tests/data/cad/climber.step
```

A browser tab opens on the **Part** tab. On the left are the **bodies** (pieces that move together) with their pieces,
in the middle the 3D view, on the right the Inspector.

**4. Say which way is up.** A box called **Where's the robot?** sits over the 3D view. If the robot lies on its side,
press **Y is up** (Inventor saves with Y up) or **Z is up**. Press **Turn front 90°** until the red arrow points to the
front of the robot. Then press **Looks right**. (If this file is only a mechanism and not a whole robot, press **This
isn't a whole robot**.)

**5. Pick the materials.** Click a piece in the 3D view (Shift-click to add more) and pick its **Material** in the
Inspector. Under **Still needed** on the left, items marked **To do** are the pieces Scrimmage still counts as
aluminum. The bottom bar shows the total weight; it updates as you go.

**6. Make something turn.** Click a body that should swing, such as the arm. In **How it moves** pick **Turns**, press
**Pick the axis**, and click the hole or round face the arm turns about. A slider named **Try it** appears: drag it
and the arm swings in the picture. If it swings the wrong way, pick the axis again. (A hinge is not saved until its
axis is picked, so you never get a guessed one.)

**7. Check the list.** Work down **Still needed** until it says "Nothing left to do." Every change is saved at once;
there is no Save button.

You should see: **Parts up to date** in the bottom bar, and a `parts` folder next to the STEP file holding your
`climber.import.toml` and one folder for each part it made.

## Put the parts in a robot

Open your robot config (`scrimmage edit my_robot.toml`), press **Import CAD…** under the parts library, pick your STEP
file, do the steps above, and press **Back to the robot**. The new parts are in the library. Press **Add**: the part
goes where the CAD has it, and the bottom bar shows the robot's center of gravity (**CG**).

## Changed the CAD?

Export it again over the same file, open the import (`scrimmage edit parts/climber.import.toml`) and press **Import
again**. Your materials, joints and motors stay.

More in [the Part tab](../part-tab.md).
