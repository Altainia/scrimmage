# Try a mechanism idea

For students, CAD designers and mentors who want to see whether a mechanism idea works before anyone builds it:
two wheels that squeeze a ball, a hood, a bar that bounces pieces up. No robot code and no programming.

[← All guides](README.md) · Needs: [the install](install.md), the first line of step 3 (`pip install -e .`)

You describe the idea in a short text file (a "bench file"), and a page in your browser (the Bench tab) lets you
change it and run it. Scrimmage feeds game pieces into the idea and draws where every piece went, with a few
plain-word results such as "3 of 3 left through `goal` at 7.4 m/s, 6° down".

> **Every result says `UNCALIBRATED`.** The numbers for the ball and the wheel treads are estimates until someone
> measures the real ones. Use the results to compare ideas with each other ("the round hood shoots higher than the
> flared one"), not as exact predictions.

## Your first five minutes

**1. Open the page.** In the `scrimmage` folder, with the venv active:

```bash
mkdir my-ideas
cp examples/bench/hooded.toml my-ideas/slower.toml
scrimmage edit my-ideas
```

(Keep your ideas in their own folder, `my-ideas`, so they never get mixed up with the examples.) A browser tab opens
on the Bench tab, with your copy of the hooded shooter. The command keeps running until you press Ctrl+C in the
terminal, so open a second terminal for anything else.

**2. Run it.** Press the blue **Run** button. In a few seconds you'll see the side picture of the shooter with the path
of every ball, and big result lines such as `10 of 10 left through shot at 8.8 m/s ± 0.0, 53° up`. A yellow
`UNCALIBRATED` line says the numbers are estimates. **Pass GIF** under the picture plays one ball going through.

**3. Change one number.** Press **Show all** in the **Change it** panel on the right, find the group headed
**shaft shooter** and its `target_rps` (the wheel's speed in turns per second; it says `66.7`). Type `33.3` (2000 rpm) and press Enter. The
number is written into `my-ideas/slower.toml`. While you are there, find the group headed **bench**, its `name` (the idea's title)
and `author`, and type your own: a name such as `slower hood`, and `Ava`.

**4. Run again.** Press **Run**. The balls now leave at about 4.9 m/s instead of 8.8. That is the whole loop: change,
run, look. The numbers you change float to the top of the panel, so the next change is one click away.

**5. Move something.** Press **Edit shape**. You see the shooter in 3D. Click the hood to select it, and use the **-**
and **+** boxes beside a number in the right-hand panel to nudge it; every number has a line of help. **Back to the
stage** returns. Run again to see what it did. (**Undo** at the top right takes back your last edit.)

**6. Put the ideas side by side.** Press **New idea**, give it a name and your name, choose `the hooded example` under
"Start from" and press **Make it**: now you have two ideas. Press **Compare**, tick both and press **Compare** in the
box: one picture and table with the ideas side by side. **Gallery** makes one page with every idea, who it's from, its
picture and its numbers; it can be emailed. These are saved in `my-ideas`, in folders named after the ideas.

More about every part of the page is in [the Bench tab guide](../bench-tab.md).

### Without the page

Everything the page does is also a command, and works on the same files. This is how a student reruns an idea at
home, or a mentor runs one on a laptop with no browser:

```bash
scrimmage bench run examples/bench/hooded.toml
```

You'll see a few result lines, then where the pictures went:

```
10 of 10 left through `shot` at 8.8 m/s ± 0.0, 53° up
...
wrote examples/bench/hooded-results/ (summary.txt, the CSVs, path_side.png, pass.png, pass.gif)
```

Open the pictures: `xdg-open examples/bench/hooded-results/path_side.png` shows the side view with the path of
every ball, and `pass.gif` in the same folder is an animation of one ball going through. (You can also double-click
them in the Files app, in `scrimmage/examples/bench/hooded-results`.) To watch it move in 3D (slowed down 4 times):

```bash
scrimmage bench view examples/bench/hooded.toml
```

It plays over and over. Close the window to get your terminal back.

To change a number by hand, open `my-ideas/slower.toml` in a text editor (Text Editor or VS Code) and find
`target_rps = 66.7`. Change it to `33.3`. To change the title, under `[bench]` change `name` and `author`. Keep the
quotes: `author = "Ava"`, not `author = Ava`. (Leave the other `name =` lines alone: they name the wheel, the hood and
so on, and other lines refer to them. The page only ever changes the one under `[bench]`.) Save it, then:

```bash
scrimmage bench run my-ideas/slower.toml
cp examples/bench/hooded.toml my-ideas/
scrimmage bench compare my-ideas/*.toml
scrimmage bench gallery my-ideas
```

`compare` prints a table and draws `my-ideas/compare-results/compare.png`. `gallery` writes `my-ideas/gallery.html`:
one page with every idea, who it's from, its picture and its numbers. It opens in any web browser and can be emailed.

## Reading the results

| A result line like | Means |
|---|---|
| `3 of 3 left through goal at 7.4 m/s ± 0.2, 35° up` | Where the pieces went, how fast, and at what angle (0° is straight ahead). The `± 0.2` is how much they varied. |
| `the kicker takes the hardest hits: up to 222 N` | The part that gets hit hardest. Worth making strong in CAD. |
| `the top shaft drops 14% and recovers in 0.05 s` | How much each piece slows the wheels. A big drop means the next piece goes out slower: try a flywheel. |
| `2 of 3 jammed` | Pieces stopped inside. The picture shows where. |

Every result folder also has `summary.txt` (everything printed, plus tables) and CSV files that open in a
spreadsheet.

## In the room at kickoff: ideas voiced to an agent

This is how the bench is meant to be used the week after kickoff. Every student gets to see their idea run, not just
the ones who can draw it.

**Set up once** (a mentor's laptop on the projector): install with the agent line (`pip install -e ".[mcp]"`) and
connect Claude Code, as in [the install's step 5](install.md#5-optional-connect-claude-code). Then make a folder for
the day's ideas and open it in the page, in one terminal window:

```bash
mkdir -p ideas/kickoff
scrimmage edit ideas/kickoff
```

Put the browser tab on the projector. Start Claude Code in the `scrimmage` folder, in a second terminal beside it.
Nobody has to ask the agent to open pictures: whatever the agent writes or runs shows up on the page by itself.

**For each idea:**

1. A student describes the idea out loud. The mentor says it to the agent, with the student's name, for example:
   *"Ava's idea: two 4 inch wheels, one above the other, squeeze the ball and fire it straight out. Make a bench file
   for it in ideas/kickoff/ and run it."* (If the agent starts somewhere else, give it the folder's full path.)
2. The agent writes the file, checks it and runs it. The idea appears on the page's stage, then its picture and the
   result lines, large enough to read from the back of the room. The line under the name says who did it.
3. The room reacts: "tighter squeeze", "move the bar up", "what if the hood went further round?". The mentor passes it
   on, and the agent edits and reruns; the page follows. The agent can pin the number being discussed on the page
   ("pin the squeeze on the screen"). A question like "which gap is best?" is a sweep: *"Sweep the squeeze from 0.15
   to 0.3 of the ball's diameter."* Or a student at the keyboard changes a number in the **Change it** panel and
   presses **Run**; the page and the agent take turns.
4. At the end, press **Gallery** on the page, or say *"Make the gallery for ideas/kickoff."* It is one page of every
   idea: put it on the projector, or send it to the team.

The idea files are only on the mentor's laptop. Afterwards, copy the `ideas/kickoff` folder to the students (a USB
stick, a shared drive, or email the folder zipped), and they can open it with `scrimmage edit ideas/kickoff` and
rerun and tweak any of them.

## Next steps

- **The page in detail:** [the Bench tab guide](../bench-tab.md) covers the stage, the Change it panel, the 3D editor
  and working with an agent.
- **The full bench guide:** [docs/bench.md](../bench.md) has every shape you can use (wheels, plates, bars, rollers,
  curved hoods, your own CAD parts), how to write a bench file from scratch, sweeps, and how accurate it is.
- **More examples:** `examples/bench/` has a toy-car pinch, a bounce bar, a curved ramp and three hood shapes. Each
  file starts with a comment explaining the idea.
- **Your CAD:** a part folder exported from FreeCAD, or STL/OBJ meshes, can go straight into a bench file. See
  [CAD in the bench guide](../bench.md#cad).
- **A shooter only:** `scrimmage launcher` is a more detailed model of a hooded ball shooter, including the flight
  to the goal. See [docs/launcher.md](../launcher.md).
- **A disc shooter:** `scrimmage disc` models a wheel-and-fence Frisbee shooter and the disc's flight to a slot
  goal (2013-style). See [docs/disc.md](../disc.md).

## If something goes wrong

- **The error names a key or a value** (`unknown key 'diamter'`, say): fix that line. The message lists the
  allowed words. `scrimmage bench check my-ideas/idea.toml` lists the first problem in each part of a file (one per entry: a wheel, a
  bar, the piece) in a moment, without running it. The page's
  **Problems** list shows the same.
- **Changing the wheel speed changes nothing.** The ball is still slipping on the wheels when it leaves (a result
  line says so: "the balls are still slipping on the wheels when they leave"), and in the model a slipping ball gets the
  same push at any speed. More grip changes it: more squeeze, wider or more
  wheels, or a hood that keeps it against the wheel for longer. This is also the least certain part of the model, so
  treat the number with care.
- **A rerun replaced my pictures.** Each run overwrites that idea's results folder. To keep both, copy the idea
  (**New idea**, with the same example), or from the command line give a run its own folder:
  `scrimmage bench run my-ideas/idea.toml --out my-ideas/idea-tight-gap`.
- **The page says "Not connected", or a button does nothing.** The `scrimmage edit` command stopped. Start it again
  and use the new link it prints. More in [the Bench tab guide](../bench-tab.md#troubleshooting).
- **Pieces fall through, fly off or jam where they shouldn't:** see
  [Troubleshooting in the bench guide](../bench.md#troubleshooting).
