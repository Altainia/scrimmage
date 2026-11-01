# Usability: possible future work

Rough edges found on 2026-10-31 while writing the [getting-started guides](getting-started/README.md), by following
each guide literally from a fresh clone and a fresh venv (Linux, Python 3.14). The readers in mind are high-school
students and mentors who aren't programmers, using it at kickoff or for their own robot. Ordered by how likely each is
to stop one of them, most likely first. Items 2, 7 and 9 have been done (see their entries) and items 4 and 15 are eased; nothing else has been started.

## Likely to stop someone

1. **DONE (2026-10-31): the bench says when more wheel speed won't help.** Each ball's exit slip is recorded
   (`exit_slip_pct` in `pieces.csv`, the launcher model's measure), and at 10% or more a headline says the balls are
   still slipping and that more grip, not more speed, is what helps. The original note: In `examples/bench/bounce_bar.toml` without the bar,
   the ball leaves at 7.397 m/s at 30, 45 and 60 rps alike. It is still slipping on the treads when it leaves, and
   Coulomb friction gives the same push at any slip speed. With more squeeze it grips, and 60 rps then gives
   9.07 m/s. This is the model working as designed, and the slip regime is also its least certain part (the
   2026-10-30 spike's answers span 6.4 to 9.2 m/s at 4000 rpm). But "make the wheels faster" is the first thing a
   student tries, and a result that doesn't move with no explanation reads as "speed doesn't matter". The launcher
   model already reports `exit_slip_pct`. A bench headline such as "the ball was still slipping when it left: more
   wheel speed won't change this; more grip will (more squeeze, more wheels, a longer wrap)" would cover it. The
   getting-started mechanism guide has a troubleshooting line for it until then.
2. **DONE (2026-10-31, the Robot code panel): a scenario report doesn't mention a CAN ID that doesn't match the code.** With `TalonFX:21` in the config and
   `TalonFX:1` in the code, the terminal logs
   `WARNING ... config expects Talon FX (v6)[21], but the robot program hasn't created it ... (the robot has ...)`,
   but the FAIL report only says the pose was 0.34 m off. The first thing a team gets wrong is the IDs, and the
   report is where they look. Carry device-binding warnings into `report.md`, `report.json` and the printed summary
   as notes, the way the Phoenix real-time note is.
   Done: `report.md`, `report.json` and the printed summary carry a device note for each model part whose device the
   code didn't make, and the Robot tab's device table shows it live and can apply the matching IDs.
3. **Windows is untested, and CAD students are on Windows.** Inventor runs only on Windows, so the students the
   bench is for will mostly have Windows laptops, possibly school-managed ones where installing Python is hard. The
   bench is pure Python plus MuJoCo and should work, but nobody has run it. CI runs only on Ubuntu. Run the bench, the
   launcher and the MCP server on a Windows machine once (after the CI budget allows, a `windows-latest` bench-only
   job), and fix what breaks.
4. **Getting it installed takes git, a private GitHub repository, a terminal and a venv.** That is four hurdles
   before the first result, and the private repo means every student needs adding by hand and signing in to clone (a
normal password doesn't work; the guide sends them to `gh auth login`). Stock Ubuntu adds two more: `python3 -m venv`
needs `python3-venv` installed with sudo, and Ubuntu 22.04's Python is 3.10, too old. `uv` solves both (it brings
its own Python and needs no sudo). Options, roughly from
   least to most work: a one-line install script; `uv tool install` or `pipx` instructions, so no venv activation is
   needed in each terminal; a wheel attached to a GitHub release; a public package. Decide what the repository's
   visibility will be first.
   *Eased a little by the Bench tab (typing, not installing):* after the install, one command (`scrimmage edit`) and the page replace the typed
   `bench` commands, `xdg-open` and text-editor steps.
5. **DONE (2026-10-31): the agent is told the bench docs' and examples' full paths** (filled in when the MCP
   server starts). The original note: **The agent is told to read "docs/bench.md in the Scrimmage repository" without the path.** If Claude Code is
   started outside the `scrimmage` folder, the agent has to find the format on its own. The guides say to start it
   in the folder. Better: put the absolute path in the MCP server's instructions (it is known at run time from the
   package's location), or serve the bench format as an MCP resource or tool.
6. **DONE for the server (2026-10-31): a committed `.mcp.json` connects Claude Code started in the `scrimmage`
   folder** (it asks once to approve); the permissions part below is still open. The original note: **Connecting
   Claude Code needs a command.** A committed `.mcp.json` in the repository (pointing at
   `.venv/bin/scrimmage-mcp`) would make Claude Code offer the server when started in the folder, with no
   `claude mcp add`. A committed `.claude/settings.json` allowing the `bench_*` tools and writes under `ideas/` would
   also stop the permission prompts that otherwise land on the projector mid-brainstorm.

7. **DONE (2026-10-31, the Bench tab): the room can't see what the agent sees.** Claude Code in a terminal gets the bench pictures, but the projector
   shows only text unless the mentor asks the agent to open each picture. A gallery page that refreshes itself as
   ideas are added (open once in a browser beside the terminal) would let the room watch every run.
   `scrimmage edit <folder>` now opens [the Bench tab](bench-tab.md): the newest idea's picture and headlines follow
   the agent's writes and runs live, on one page beside the terminal, and the agent can pin the numbers being
   discussed (`bench_focus`). The kickoff guide uses it.
8. **Kickoff ideas stay on the mentor's laptop.** Students rerunning an idea afterwards need the files copied to
   them. A shared place for each season's ideas (a folder in a team repository, or the gallery page with the bench
   files inside it to download) would close the loop.

## Friction a newcomer can get past

9. **DONE (2026-10-31, the Robot code panel): `scrimmage edit --new swerve` writes only the RobotPy `[robot_program]`, commented out.** A Java team (most
   CTRE teams) has to find the Gradle line in the README. Offer `--lang java` or detect a `build.gradle` in the folder
   above. Further: read a Tuner X project's `TunerConstants.java` for the CAN IDs, gear ratios and module type, which
   is where a CTRE team would otherwise copy them from by hand.
   Done for the page: the Robot tab's **Robot code** panel detects a Java (GradleRIO) or RobotPy project and writes
   `[robot_program]`. The `--new` template still writes only the commented RobotPy example, now with a pointer to the
   panel. Not done: `--lang java` or reading `TunerConstants.java`.
10. **DONE in part (2026-10-31): a scenario with no checks says so in its report** ("passing only means the robot
   program ran"); `--new-scenario` still writes no active check. The original note: **The starter scenario has no checks,
   so it always passes.** A first PASS that tested nothing is misleading.
   Have `--new-scenario` include a commented pose check, or have `scrimmage run` warn "this scenario has no checks".
11. **`scrimmage run` takes one scenario.** The test guide has a shell loop for "run them all". Accept several files
   or a folder, run them in turn, and print one summary with an exit code for the lot.
12. **Reports land in `runs/` in the team's robot repository**, which their `.gitignore` doesn't cover. Writing a
    `runs/.gitignore` containing `*` when the folder is created would keep it out of commits with no setup.
13. **Passing samples print warnings.** `scenarios/fastmode_auto.toml` passes but prints eight warnings (six fast-mode
    motors with no binding, two encoders with no device ID). A newcomer learns from this to ignore warnings, which
    then hides the useful ones (item 2). Bind them in the sample config, or quiet the expected ones.
14. **The "neutralized N robot-side sim writes" note shows on every Tuner X robot.** It is expected, but "neutralized"
    plus a big count reads like an error. Say that it is normal: "turned off the robot code's own simulation (Tuner X's
    sim thread), as expected: physics drives the sensors."
15. **A bench file has many `name =` keys.** The guide's "change the name" step had to say "the one under `[bench]`",
    because a find-and-replace on `name` renames every shape and the file stops loading (`two entries named ...`).
    Consider `title` for the bench's own name.
    *Eased by the Bench tab:* it edits the bench's own title (`bench.name`) and no other `name`, so a student using the
    page can't hit the trap; the by-hand step in the guide still has it.
16. **Bench results go beside the bench file**, so running an example writes into `examples/bench/` (git-ignored,
    but surprising when the examples folder fills up). The guides tell students to copy examples into their own folder
    first.
17. **No tested CI recipe for a team's robot repository.** The test guide says GitHub Actions is possible but gives
    no workflow, because none has been run, and installing a private Scrimmage in CI needs a token. Write and test
    one, ideally fast mode for CTRE robots.
18. **The README is a 900-line reference.** It now points newcomers at the guides first. Splitting it into topic
    pages (most sections already have one in `docs/`) would leave a short README; worth doing at the next large docs
    change.

## Worth a closer look

19. **A bench recovery time that may be wrong.** `examples/bench/hooded.toml` at 33.3 rps reports "the `shooter`
    shaft drops 6% and recovers in 1.93 s", against 0.10 s at 66.7 rps. A slower wheel has more motor headroom, so a
    20 times slower recovery is suspicious: check the controller (P only, `kP = 0.2`) and the 2% recovery tolerance
    near the lower target before trusting the headline.
20. **Only three swerve modules are stock parts** (SDS MK4i and MK4n, REV MAXSwerve). Teams using others (WCP,
    Thrifty, newer SDS) have to approximate. Add the common ones.
21. **The lockstep extension must be reinstalled by hand after some updates.** Scrimmage says when, but a
    `git pull` followed by an error is a poor first experience. Checking the version and reinstalling on demand, or
    folding it into the main install, would remove a step.
