// The Part tab's inspector sections (registered with part.js's addSection, shown in this order when their
// `when(ctx)` holds) and the "Where's the robot?" step over the 3D view. part.js is the store, the edits and the tree.
//
//   summary      nothing selected: the part's weight and center of gravity, and a "Try it" slider per joint
//   pieces       the selected pieces: their weight and their body
//   material     what the selected pieces (or the selected body's pieces) are made of, or a typed weight
//   body         one body: its name, weight, center of gravity, and whether it is its own part
//   joint        one body: "Moves: no (welded) / turns / slides" relative to its parent, the axis (picked in the
//                3D view, or snapped to x, y or z; a new turn or slide is saved only then), "More" (range, spring,
//                damping, friction) and "Try it"
//   slots        one body: its motor (count, gear ratio, spool diameter for a slide, in the chosen units; stored
//                in meters), encoder (none, on the joint, on the motor) and gyro
//   mounts       one body: the spots another part bolts on ("Add mount": click a flat face, then name it)
//   collision    one body: how it touches things, with a one-click suggestion for hooks and wheels (never applied
//                by itself), and for a body that touches things its grip material (set_grip: the pads' friction, the
//                same picker as the Test tab's Setup)
//   links        one body, under "More": couplings ("turns with another joint") and loops ("pinned to another body")
//   bodies       several bodies: one button each
//   frame        the robot frame: which way is up and forward, and a button that shows the step again
//
// Plugin mode (a part folder from the FreeCAD add-on) keeps only what its overlay can change: weight, collision and
// a joint's range, spring, damping, friction and stops (how hard the range's ends are: limit_solref); its motors,
// encoders and mounts are slot names, shown as text.
//
// Picking in the 3D view (an axis, a floor, a center, a mount, a pin) goes through view3d.startPick: a hint shows
// over the view and Esc (or Cancel) stops it. "Try it" only moves the drawing (part.tryit): it never makes an edit.
// Points and axes go to the server in CAD coordinates; part_frame.js converts them from and to the robot frame.
//
// Each group lives in its own file, imported below in this order (the order they are registered in, so the order
// they show in): part_bodies.js (summary, pieces, material, body, bodies), part_where.js (the step and frame),
// part_motion.js (joint, tryall, slots), part_hardware.js (mounts, collision, links); part_helpers.js holds what
// they share.

import "./part_bodies.js";
import "./part_where.js";
import "./part_motion.js";
import "./part_hardware.js";
