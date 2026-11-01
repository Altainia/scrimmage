# Getting started

Scrimmage is a robot simulator for FRC teams. It can try a mechanism idea with game pieces before anyone builds it,
and it can run your team's real robot code on a simulated robot before the real one exists.

These guides get you from nothing to a first result as quickly as possible. Each one is a few steps, every command can
be copied as it is, and each step says what you should see. Start with the install, then pick the guide for what you
want to do.

| I want to... | Guide | Who it's for | Programming? |
|---|---|---|---|
| Set it up on my computer | [Install Scrimmage](install.md) | everyone, once | no |
| See whether a mechanism idea works: wheels, hoods, bars, ramps | [Try a mechanism idea](try-a-mechanism.md) | students, CAD designers, mentors | no |
| Turn our CAD into parts with weights and joints | [Bring your CAD in](bring-your-cad-in.md) | CAD students, mentors | no |
| Run our robot code on a simulated robot, and drive it | [Run your robot code](run-your-robot-code.md) | programmers | a little: you edit a text file |
| Check automatically that our autos and mechanisms still work | [Test your robot code](test-your-robot-code.md) | programmers | a little |

**With an AI agent.** A mentor can connect Claude Code to Scrimmage ([install step 5](install.md#5-optional-connect-claude-code))
and ask it in plain words: "make a bench file for Ava's idea and run it", "run our auto and show me the top view",
"why does the elevator overshoot?". The kickoff brainstorm in [Try a mechanism idea](try-a-mechanism.md#in-the-room-at-kickoff-ideas-voiced-to-an-agent)
works this way, with the Bench tab (`scrimmage edit`) beside it so the room sees every run.

## Words you'll see

| Word | Means |
|---|---|
| **terminal** | The window where you type commands. On Linux, open "Terminal" from the app menu. |
| **venv** | The Python environment Scrimmage is installed in. Turn it on in each new terminal with `cd scrimmage`, then `source .venv/bin/activate`. |
| **TOML file** | A plain text file of `key = value` lines, such as `diameter = 0.1016`. Open it in any text editor. Everything you describe to Scrimmage is one. |
| **Bench tab** | The page `scrimmage edit` opens to try mechanism ideas: change a number, press Run, see the picture. See [the Bench tab guide](../bench-tab.md). |
| **bench file** | A TOML file that describes a mechanism idea for the component bench. |
| **robot config** | A TOML file that describes your robot (its parts and their CAN IDs) and how to start its code. |
| **scenario** | A TOML file that runs your robot through some steps (disabled, auto, teleop) and checks the results. |
| **UNCALIBRATED** | The numbers are estimates until someone measures the real ball or wheel. Good for comparing ideas, not for exact predictions. |
| **lockstep** | Scrimmage controls the robot code's clock, so runs are faster than real time and repeat exactly. Linux only. |
| **MCP** | How an AI agent such as Claude Code talks to scrimmage. |

## After these guides

The [main README](../../README.md) is the full reference: every command, option and file format. The topic guides go
deeper: [the Bench tab](../bench-tab.md), [component bench](../bench.md), [launcher model](../launcher.md), [disc launcher](../disc.md), [robot parts](../parts.md),
[FreeCAD export](../freecad.md), [the assembler](../assembler.md), [the Part tab](../part-tab.md), [driving in the browser](../drive.md),
[scenario editor](../scenario-editor.md), [fast mode for CTRE robots](../fast-mode.md), [REV robots](../rev.md),
[test range](../test-range.md), [terrain](../terrain.md) and [climbing](../climbing.md).
