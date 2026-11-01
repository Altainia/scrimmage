# Bench tab and start page: design

Sub-project 15. Branch `bench-tab`, from `getting-started` (which is based on `component-bench`).

## Why

At kickoff the mentor voices each student's mechanism idea to Claude Code on a laptop wired to the projector, and the
agent writes and runs bench files. Today the room sees only the terminal's text: the pictures the agent makes stay in
files nobody opens (usability item 7 in `docs/usability-future-work.md`). Afterwards, students who want to tweak an idea
must copy files, edit TOML in a text editor and type commands. The project owner wants as little as reasonable left to
the command line or to hand-edited text files, with help and context-aware editing in their place. This is the first of
several projects toward that (the order agreed on 2026-10-31: this one; forms for the rest of the robot config; run all
scenarios from the page; a desktop launcher or installer; later, field tools and a TunerConstants import).

## Who and where

- The computer driving the projector, and only it. One person at a time touches the page; students don't connect from
  their own devices. The page stays local like `scrimmage edit` today (127.0.0.1, token in the link).
- Usually with an agent: Claude Code in a terminal beside the page, using the `bench_*` MCP tools. Also without one: a
  student opening a folder of ideas later.

## Goals

1. **A Bench tab in the `scrimmage edit` app**, beside Robot, Scenario and Drive, for one idea session (a folder of
   bench files).
2. **Live with the agent.** When the agent writes or edits a bench file, or runs one, the page shows it within a moment,
   with no reload. When someone edits on the page, the bench file on disk changes at once, so the agent sees it.
3. **Stage layout** (chosen from three mockups):
   - The newest idea, the one last run or edited by anyone, fills the screen: author, name, the side path picture, and
     the headlines in large type, the UNCALIBRATED line included.
   - A "Change it" panel beside it.
   - A strip of the session's earlier ideas along the bottom. Clicking one brings it on stage.
4. **"Change it" panel.**
   - It shows the few numbers being talked about. The agent can pin numbers there through MCP.
   - A number anyone changes (agent or hand) moves to the top.
   - "Show all" opens every editable value of the idea.
   - **Run** reruns it.
5. **Editing an idea's shape** (chosen: a 3D view like the Robot tab, not dragging on a side drawing).
   - **Edit** shows the mechanism in 3D: orbit, click a part to select it.
   - Place parts with number boxes and +/- buttons (10 mm and 5° steps, as in the assembler).
   - Add shapes from buttons (wheel, plate, bar, hood or curve, inlet, outlet, CAD part) and delete them. Edit shafts,
     the piece and the run settings in forms.
6. **Help while editing.**
   - Every field says in plain words what it is, with units ("spring: how hard the bar pushes back per meter it gives,
     N/m").
   - Choices are lists, not typing: piece presets, treads, materials, motors, and which shaft a wheel is on.
   - Problems show as you type, before a run, from the same checks `bench check` makes ("the inlet is inside the
     hood", and the new overlap warnings such as "the bar overlaps the top wheel"), with the bad field marked.
7. **Session tools on the page.** Sweep (pick a value, from, to, steps; the results shown on stage), Compare (pick
   ideas) and Gallery (write and open the folder's `gallery.html`), using the existing `bench sweep|compare|gallery`
   code.
8. **Start page.** `scrimmage edit` with no file opens a start page:
   - recent robots, scenarios and idea sessions, and browsing for others;
   - **New robot** (swerve, REV swerve, tank: the existing templates);
   - **New scenario** (pick the robot);
   - **New idea session** (a folder, such as "kickoff 2027");
   - in a session, **New idea**: the student's name and the idea in a sentence, then a blank bench or one of the
     examples to start from.

   The command-line forms (`edit FILE`, `--new`, `--new-scenario`) keep working.

## Non-goals

- Students' own devices, network access, or several people editing at once.
- Moving the bench to a new engine or changing its physics or results. The tab shows what `run_bench` produces.
- The other projects in the order (robot-config forms, run-all, launcher/installer, field tools). The start page only
  creates robots and scenarios from the existing templates.
- Replacing the CLI or MCP tools. They stay, and CI and scripts keep using them.

## Anti-goals

None named by the owner. The builder's reading of the brief is the guard: never show a result without its UNCALIBRATED
line; never lose a hand edit or an agent edit to the other; never leave the file and the screen disagreeing silently.

## Constraints

- **Build on the existing app** in `scrimmage/assembler/`: its server (token, 127.0.0.1, JSON API), its tab layout,
  its three.js 3D view (`view.py` already serializes a compiled MuJoCo model; `bench.build.build_bench` compiles a bench
  to one), its inspector style, and its document handling (`doc.py`: tomlkit edits that keep comments and order, undo
  and redo, atomic writes, reload when the file changes outside).
- **Bench files stay the source of truth**, in the existing format (`scrimmage/bench/spec.py`). Edits keep comments
  and order. A file the page can't fully edit opens with what it can't edit marked read-only rather than refused, the
  way the assembler handles it.
- **Runs** use `run_bench` and the existing pictures and report code, one at a time. A run writes the same results
  folder the CLI and MCP do, so the page also shows results the agent produced.
- **Help texts and choices** come from one place the format already defines (the dataclasses and tables in
  `bench/spec.py`, `presets.toml`), so the docs, `bench check` and the page can't drift apart.
- **One simulation at a time** on the machine, as with the Drive tab. A bench run doesn't start a robot program, so it
  doesn't need the NetworkTables port.
- Tests as for the rest of the app: server and edit logic in pytest, and the page in the existing Playwright browser
  tests.
- Commits end with the Co-Authored-By line. Verify locally; the CI budget resets on 2026-11-01. PR order: after 13 and
  14.

## Decided with the owner

- The page runs only on the projector computer.
- Edits from anyone show everywhere.
- Stage layout.
- "Talked about" numbers, with hand-edited numbers bumped up.
- Shapes are editable.
- 3D view with number boxes, not dragging.
- Plain-word help and lists.
- Start page with New buttons.
- Order of projects.

## My calls (the owner may override)

- **It's a tab of `scrimmage edit`**, not a separate command.
- **How the page notices agent edits:** the server watches the session folder (bench files and results folders), and
  the page polls or holds a socket, as the Drive tab does for live state.
- **Pinning is a new MCP tool**, `bench_focus(path, keys)`. The order of the talked-about list is kept beside the
  session (for example a small JSON file in the folder), so it survives a page reload.
- **New idea from a sentence** fills `[bench] author` and `idea`. Turning the sentence into shapes is the agent's job,
  not the page's.
- **Edits are saved at once** (like the assembler), not on a Save button. Undo and Redo cover them.

Settled after the builder check:

- **Help texts live in one table in the package.**
  - The per-key explanations, units and defaults now sit in about 95 rows of `docs/bench.md`. They move into a
    machine-readable table in `scrimmage/bench/` (for example `fields.toml`: key path, unit, one-line plain help,
    choices) that the page reads.
  - A test checks that the table covers every key the parser accepts, and that every key in it appears in
    `docs/bench.md`'s format reference. The docs are not generated from it.
- **`bench check` may change.**
  - It collects every problem in a file, each with its key path (`[[bar]] 'kicker' center`), instead of stopping at the
    first. The CLI and MCP print them all, and the page marks each field.
  - New shape-against-shape overlap checks ("the bar overlaps the top wheel") are **warnings**, not errors, because
    some overlaps are on purpose. Existing files and examples must still load.
- **One run at a time includes the agent's.**
  - A lock file in the session folder covers runs from the page, the CLI and the MCP tools.
  - A second run waits. The page shows "the agent is running Ava's idea" while the lock is held.
  - The MCP `bench_*` tools take the lock too.
- **Values from an `include`d file show read-only**, with where they come from and an **Edit here instead** button.
  The button copies the entry into this idea's file as an override, so editing one idea never changes another.
- **One thing open at a time.** The app holds either a robot (Robot, Scenario and Drive tabs, as today) or an idea
  session (the Bench tab). The start page switches between them. `edit FILE` given a bench file, or a folder of them,
  opens it as a session.
- **The page learns about every run from the session, not only from the folder.**
  - When a bench file sits in a session folder, the MCP tools and the CLI append each run, sweep and compare (with its
    results folder, wherever that is) to a small log in the session, the same place `bench_focus` writes.
  - The page watches that log and the bench files.

## Left to the builder

- API shapes, file layout, how the 3D view distinguishes bench shapes, polling versus sockets.
- How the sweep form maps onto `[sweep]`.
- The start page's recent-files store.
- How "Show all" groups fields.
- Whether a run's live progress shows.

## Decisions made during the build

Rulings taken while building (2026-10-31), each with what it costs if wrong.

- **Opening a file while the agent runs.** Opening another file is refused only while this page's own run is queued or running, never because the agent holds the session's run lock. Cost if wrong: an agent run in progress during a switch shows in no tab until reopened.
- **Field help.** Every key's help lives in `scrimmage/bench/fields.toml`, and a test checks that each key also appears in docs/bench.md. Cost if wrong: small doc additions.
- **One error per entry.** `bench check` collects one error per entry (the first) and skips that entry, so a student fixes one field of a wheel at a time. Cost if wrong: an extra check round per entry.
- **Talked-about keys.** A changed or added entry reports its entry key plus every key inside it except `name`. Cost if wrong: one redundant focus entry.
- **Problem keys use the meters name.** Keys read `wheel.top.diameter` even when the file writes `diameter_in`; the page marks either form. Cost if wrong: the page maps both forms.
- **The run lock is per session folder,** as this design says, not per machine. Cost if wrong: two folders' runs share the CPU, slower but never wrong.
- **An unwritable bench folder still runs,** without the shared log and lock and with one warning. A logging failure never hides a run's own outcome. Cost if wrong: such runs don't reach the Bench tab.
- **A blank idea has two shafts (top and bottom).** Two wheels on one shaft must share an axis line. Cost if wrong: none.
- **A new wheel goes on a free shaft,** or gets its own shaft. Cost if wrong: an extra shaft to delete.
- **Names.** Names change only through Rename, which rewrites every reference to them. `bench.name`, the idea's title, is edited directly. Cost if wrong: none.
- **The idea on stage disappears** (deleted or renamed outside): the page shows a notice and moves to the next idea. Cost if wrong: none.
- **Energy-check failures.** A run whose energy check fails is one signal for every source: `latest.energy_failed`. The run itself counts as completed, and the Stage shows it as a failure in plain words. Cost if wrong: the job list says "done" for a run the Stage marks as failed.
- **No undo or redo shortcuts while the start page is shown.** Cost if wrong: none.
- **Projector polish found in review.** Backticked words show bold, the gallery tab can't reach back to the editor, the side picture spans the stage, and a selected shape is outlined. Cost if wrong: none.
- **Half-typed numbers are saved only by Enter or a real change,** never by a redraw. A stale edit is refused and the page names the field that wasn't saved. Cost if wrong: none (this guards Review Focus 1).
- **Moving a bar by its middle is one edit:** one write and one undo step. This uses a multi-key `set` on the server. Cost if wrong: none.
- **Sweeps and compares are the session's result,** separate from each idea's own run. The Stage shows the newest result's chart and a table of its numbers; clicking an idea shows that idea's run. Cost if wrong: one more thing on stage to explain.
- **Parked for the next round:**
  - Done afterwards (quick wins, 2026-10-31): while a compare is on stage, Run, Edit shape and Change it name the
    idea they act on ("Run bounce bar"); a sweep puts its exit-speed chart first.
  - A result whose ideas were all deleted is drawn over the remaining idea.
