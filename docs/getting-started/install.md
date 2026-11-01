# Install Scrimmage

You do this once per computer. It takes about five minutes, most of it downloading.

[← All guides](README.md)

## What you need

- **A computer running Linux.** That is where Scrimmage is tested. On Windows and macOS the component bench
  should work, but nobody has tried it yet, and robot code runs only in real time there (see
  [What works where](#what-works-where)).
- **Python 3.11 or newer.** Type `python3 --version` in a terminal to check.
- **git**, a GitHub account, and access to the Scrimmage repository (it is private: ask the mentor who runs it to
  add you).

The commands in these guides are for Linux. On Windows or macOS, ask your mentor first.

## 1. Get the code

Open a terminal. It starts in your home folder, which is a fine place for it. Then:

```bash
git clone https://github.com/Altainia/scrimmage.git
cd scrimmage
```

Because the repository is private, the clone asks you to sign in, and GitHub doesn't accept your normal password
there. The easiest fix is the GitHub CLI: install it (`sudo apt install gh`), run `gh auth login` once and follow its
prompts, then run the clone again. Or ask your mentor for a personal access token and paste it as the password.

`cd scrimmage` moves the terminal into the new folder. Every command in these guides is typed in this `scrimmage`
folder unless a guide says otherwise.

## 2. Make a Python environment

A Python environment (a "venv") keeps Scrimmage's libraries apart from everything else on the computer.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Your prompt now starts with `(.venv)`. **Every new terminal needs this again.** A new terminal starts in your home
folder, so type both lines:

```bash
cd scrimmage
source .venv/bin/activate
```

If a command later says `scrimmage: command not found`, this is why.

On Windows (untested) the second line is `.venv\Scripts\activate`.

## 3. Install the parts you need

Pick the line for what you are going to do. Each line includes the ones above it, so you can always add more later.

| You want to | Type | Takes about |
|---|---|---|
| Try mechanism ideas (the component bench) | `pip install -e .` | 1 minute |
| ...with an AI agent (Claude Code) | `pip install -e ".[mcp]"` | 1 minute more |
| Run robot code (RobotPy or Java) | `pip install -e ".[robotpy,nt,mcp]"` and then `pip install ./lockstep` | 3 minutes more |

The `-e` matters: it makes scrimmage run from this folder, so `git pull` updates it without reinstalling. After a
`git pull` that changes `lockstep/`, run `pip install ./lockstep` again (Scrimmage tells you when it is needed).

**Java teams** also need WPILib installed (the normal WPILib installer, which brings its own Java), and then once:

```bash
scrimmage java build
```

Run `scrimmage java build` again whenever you update scrimmage.

**Team folders (optional).** If your team shares robot files, each computer says where the team's folders are, once:
run `scrimmage edit`, and on the start page find **This computer's team folders**. **Browse…** sets the **CAD
folder** (robot files and parts) and the **code folder** (the programmers' projects). A robot file then says "the code
folder" instead of one laptop's path, so the same file works on every computer. If you open a file that needs a
folder this computer hasn't set, the page asks for it. (`SCRIMMAGE_CAD` and `SCRIMMAGE_CODE` set them from the terminal
instead and win over the page.)

## 4. Check that it works

```bash
scrimmage bench run examples/bench/pinch_car.toml
```

In about a second you should see lines ending like this:

```
3 of 3 left through `shot` at 6.9 m/s ± 0.0, 5° down
...
wrote examples/bench/pinch_car-results/ (summary.txt, the CSVs, path_side.png, pass.png, pass.gif)
```

To see the picture, type `xdg-open examples/bench/pinch_car-results/path_side.png`, or open the Files app, go into
`scrimmage`, then `examples`, `bench`, `pinch_car-results`, and double-click `path_side.png`. If you got this far, the install works.

Now open the page you'll use for ideas (it shows the same picture, and lets you change the idea and run it again):

```bash
scrimmage edit
```

A browser tab opens on a start page. Press **Browse…**, go into `examples`, `bench`, and open `pinch_car.toml`. You
should see the Bench tab with "pinch car" at the top, a **Run** button and a **Change it** panel. Press Ctrl+C in the
terminal to stop it. (If no tab opens, copy the link the command printed into a browser.) The page is explained in
[Try a mechanism idea](try-a-mechanism.md) and [the Bench tab guide](../bench-tab.md).

## 5. Optional: connect Claude Code

If a mentor will drive Scrimmage through an AI agent (the kickoff brainstorm in
[Try a mechanism idea](try-a-mechanism.md) works this way), Claude Code connects to Scrimmage by itself. The agent then
works beside the page: what it writes and runs appears there. You need Claude Code installed and signed in first,
which needs a paid Claude account: see [the Claude Code setup page](https://docs.claude.com/en/docs/claude-code/setup).

Start Claude Code by typing `claude` **in the `scrimmage` folder**. The first time, it asks whether to use the
`scrimmage` server this folder offers (its `.mcp.json` file): say yes. Type `/mcp` in Claude Code to see Scrimmage
listed as connected.

To use Scrimmage from Claude Code started in another folder too, add it for your whole account once, from the
`scrimmage` folder with the venv active:

```bash
claude mcp add --scope user scrimmage -- "$(pwd)/.venv/bin/scrimmage-mcp"
```

The first time the agent writes a file or uses a Scrimmage tool, Claude Code asks your permission. Choose the option
that doesn't ask again for this folder, so the questions don't interrupt the room later.

## What works where

| | Linux | Windows, macOS |
|---|---|---|
| Component bench, launcher model | tested | should work; untested |
| Robot code, by hand or headless | tested, repeatable, faster than real time | real time only; untested |
| Agents, the browser editor and Bench tab | tested | untested |

## If something goes wrong

- **`scrimmage: command not found`**: the venv isn't active in this terminal. Run `source .venv/bin/activate`
  in the `scrimmage` folder.
- **`ensurepip is not available`** at step 2: run `sudo apt install python3-venv`, delete the `.venv` folder, and
  try step 2 again.
- **`ERROR: Package 'scrimmage' requires a different Python`**: your Python is older than 3.11 (Ubuntu 22.04 has
  3.10). Ask your mentor to help install a newer one, then delete the `.venv` folder and start again at step 2.
- **Pictures fail with a GL or "could not create context" error** (usually a computer with no graphics card, or a
  remote server): install Mesa's software renderer (`sudo apt-get install libosmesa6`), then
  `export MUJOCO_GL=osmesa` before running.
- **`scrimmage java build` can't find Java**: install WPILib, or set `JAVA_HOME` to a Java 17.
- Anything else: copy the whole error message to the mentor who runs Scrimmage (or paste it into Claude Code).
