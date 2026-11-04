# Calibration: measuring, averaging and fitting (sub-project 25)

## Intent

Scrimmage marks its results UNCALIBRATED because most of its numbers are estimates: a ball's squeeze and bounce,
the friction of a wheel or a pad, a pneumatic valve's flow. The docs say how to measure several of them, but a
student who measures has nowhere to put the trials, nothing that averages them or spots a bad reading, and no way
to fit the numbers that can only be found by comparing a real shot with the model. Their result still says
UNCALIBRATED afterward.

The owner asked for:
- **A measurement file per mechanism.**
- **A command that fits the uncertain inputs to measured outcomes and reports the fit.**
- **Labels showing which numbers are measured and which are estimates**, so a result drops UNCALIBRATED only when
  its numbers were measured.
- **Illustrated how-to pages:** each test shows a picture of what is measured and how.
- **A printable form for each test,** with room for several trials that are averaged, to account for error.

The owner told me to answer the design questions myself; every call is under "My calls".

The audience is high-school students with a bathroom scale, a tape measure, a spring scale and a phone that films
slow motion, and their mentors. The standing anti-goal applies: **no unnecessary friction**.

## Goals

1. **Six tests**, each with an illustration, plain steps, a printable form, a page in the web app to enter trials,
   and a way to turn trials into a number:
   - **Ball size and mass:** a kitchen scale and a tape around the ball. Gives `mass` and `diameter`.
   - **Ball squeeze:** a bathroom scale and two boards. Gives `stiffness_n`.
   - **Ball drop:** an 18 in drop filmed against a ruler. Gives `restitution`.
   - **Sled pull:** a spring scale pulling a weighted sled. Gives a sliding friction for any two materials: a wheel's
     tread on a ball, a grip pad on a piece, a piece on the carpet.
   - **Launcher shot:** a level shot filmed in slow motion against tape marks, at two or more wheel speeds. It is fit
     with the launcher model to give the ball's `stiffness_n` and the wheel's `friction`.
   - **Cylinder stroke:** a stroke filmed or timed, with the rod's load. It is fit with the pneumatic model to give
     the valve's `conductance`.
2. **A measurement file**, `<name>.measure.toml`:
   - **What it holds:** what was measured, who measured it and when, the tools, and every trial's raw readings in
     the units the student read them in (lb, oz, kg, g, in, mm, N).
   - **What it adds:** the averages and the fit are worked out from the trials each time the file is read, so
     they can never go stale.
3. **`scrimmage measure`:**
   - **Reading a file:** `scrimmage measure <file>` prints each trial's value, then the average, the lowest, the
     highest and the spread, with a warning when the trials disagree. For a launcher or cylinder file it also prints
     the fit: the fitted numbers and each trial's measured value against the model's.
   - **Printing a form:** `scrimmage measure form <test> [-o form.html]` writes a printable blank form.
4. **Using a measurement.** A measurement file can stand in wherever its number is used today:
   - **Balls:** a launcher setup's `[ball]` and a range's ball take `measured = "<file>"` on top of a preset, and
     the file's measured fields replace the preset's.
   - **Wheels:** a launcher setup's `[wheels]` takes `measured = "<file>"`.
   - **Grip and surfaces:** a grip and a piece's surface friction take a measurement file in place of a material or
     a number.
   - **Cylinders:** a stock cylinder takes `measured = "<file>"`.
5. **Labels:** a result names what it rests on.
   - **Where:** the launcher model's report and charts, the Test tab's run notes, a bench's last line, the MCP
     results and `scrimmage air`.
   - **What it says:** which numbers are measured (from which file, by whom, when, and from how many trials) and
     which are still estimates.
   - **When UNCALIBRATED goes away:** the word appears only while an input that matters is still an estimate. For a
     launcher those inputs are the ball's stiffness, its bounce and the wheel's friction. For a grip it's the pad's
     friction, and for a cylinder its valve.
6. **The Measure tab** in the web app (`scrimmage edit <file>.measure.toml`, or "Measure something" on the start
   page). For the chosen test it shows:
   - the illustration and the steps;
   - a table to type trials into, with the average, spread and warnings updating as you type;
   - Save, which writes the measurement file;
   - "Print a blank form";
   - for the launcher and cylinder tests, "Fit" with its results.
7. **Docs:**
   - **docs/measuring.md:** one section per test, with the illustration, the steps, a link to the form, what the
     number feeds, and how the averaging and the fit work.
   - **Pointers:** the existing "measure your own" passages point to it (launcher.md, test-tab.md, pneumatics.md,
     test-range.md, bench.md).

## Non-goals

- **Other fits:** fitting a disc launcher, a drivetrain, climbing hooks or field elements. The measurement file is
  built so a later test can be added, and the docs say which are not yet covered.
- **A shared library of measured presets** across teams: a later step, and the file format allows it.
- **Reading video automatically.** The student counts frames and reads tape marks; the page shows how.
- **Statistics beyond what a student can check by hand** (C3).

## Anti-goals

- **No measured value may be worse than the estimate it replaces without saying so.** A fit that doesn't match
  (large leftover error, a number pinned at its limit, too few trials) says so in plain words and doesn't drop the
  UNCALIBRATED label.
- **No change for files that use no measurement:** every existing output stays byte-for-byte except the
  UNCALIBRATED line's wording, which becomes more specific (C7).
- **No unit traps.** Every reading names its unit in its key, and a missing or unknown unit is an error that lists
  the ones allowed.

## Constraints

- **No new dependencies:** no scipy. The fits use a bounded one- and two-parameter search in numpy.
- **Same models:** a fit runs the same model the simulator uses (scrimmage/launcher's Launcher, scrimmage/pneumatics'
  AirSystem), so a fitted number means the same thing in a run.
- **Pictures:** illustrations are hand-made SVG files (docs/img/measure/*.svg) that work in the docs, the web app and
  the forms, and read clearly in black-and-white print.
- **Forms:** printable HTML at US Letter and A4 (CSS print rules), black-and-white friendly, with no scripts needed
  to print.
- **Spellings and wording:** American spellings and plain words.

## My calls (the owner can overrule any)

**Averaging and spread**
- **C1 Trials:** a form has room for 8 trials. 3 is the least the averaging accepts (fewer reads "take at least 3").
  5 is recommended.
- **C2 What a trial gives:** each trial gives one value; the result is the mean of the trials. The spread is the
  highest minus the lowest, as a percent of the mean.
- **C3 Warnings, kept to what a student can check by hand on the form:**
  - **Spread too wide:** a spread over 15% says "your trials disagree by 22%: measure again or take more trials".
  - **Outlier:** a trial more than twice the average distance from the mean is flagged, "trial 4 looks off: check
    it". It is not dropped automatically.
  - **The form's own boxes:** average, lowest, highest, spread %, and a box to tick when the spread is under 15%.
- **C4 Units:** each reading's key ends in its unit (`force_lb`, `force_kg`, `force_n`, `drop_in`, `drop_mm`,
  `mass_g`, `mass_oz`...). Scale readings in kg or lb are treated as force (×9.81, ×4.448).

**Tests**
- **C5 Squeeze:**
  - **The ideal reading:** the force at a squeeze to 75% of the diameter, minus the top board's weight (the form
    has a box for it).
  - **A different squeeze:** a trial may give its own `squeezed_to` height; it's converted to the 25% force with the
    ball's `stiffness_exponent` (1.5 by default).
- **C6 Drop:**
  - **Formula:** `restitution = sqrt(bounce / drop)`, both measured to the bottom of the ball.
  - **Height:** the model's foam is tuned for an 18 in drop, so other drop heights are allowed but warned.
  - **Floor:** the floor kind is recorded, and carpet is warned (the model expects a hard floor).
- **C7 Label wording:** UNCALIBRATED stays as the word for "something that matters is an estimate". The line lists
  the estimates by name. When everything that matters is measured, it reads "Calibrated from measurements: ..."
  (files, dates, trial counts).
- **C8 Launcher fit:**
  - **Parameters:** the ball's `stiffness_n` (search range 0.25× to 4× the preset) and the wheel's `friction` (0.2
    to 2.0).
  - **Goal:** minimize the root-mean-square relative error of exit speed over the shots.
  - **Search:** a 9 × 9 log/linear grid, then a local refinement.
  - **One wheel speed only:** with only one wheel speed measured, only stiffness is fit (friction stays at its
    current value) and it says why ("shoot at a second wheel speed to fit friction too").
  - **A good fit:** RMS error under 5%, and neither number within 5% of its search limit. Otherwise the fit is
    reported but keeps the UNCALIBRATED label.
  - **A shot's exit speed:** the tape mark spacing × fps / frames counted.
- **C9 Cylinder fit:**
  - **Parameter:** `conductance`, from 0.1× to 10× the default, by bisection.
  - **What's matched:** the measured extend time, from the valve opening to the rod at full stroke.
  - **The model:** the pneumatic AirSystem plus a one-body rod model (rod mass plus the stated load mass, seal
    friction, and gravity if the file says the stroke is vertical).
  - **A good fit:** within 5%.
- **C10 Sled pull:** `friction = pull force / weight on the sled`, read at a steady slide (not the first jerk; the
  page says so). The file names the two materials ("wheel tread: 40A", "FUEL"), so a reader knows where it applies.

**Where measurements are used**
- **C11 Which files point to which:** a measurement file says what it measured (`kind`, and `base` for a ball: the
  preset it measured). Pointing a ball setting at a friction file is an error that names both.
- **C12 Paths:** a measurement file path is resolved relative to the file that names it.

**The Measure tab**
- **C13 Tab rules:** a Measure tab like the others. It opens for `.measure.toml`, and the start page's "Measure
  something" makes a new file in the folder. Unsaved edits get the same prompt as the other tabs.

## Left to the builder

- The internal module layout (suggested: `scrimmage/measure/`), and how provenance travels with each input.
- The SVG artwork, within the constraints.
- The exact form layout and wording, within the plain-words rule.

## Decisions made during the build

(Filled in during the build.)
