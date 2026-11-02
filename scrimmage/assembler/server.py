"""The editor's local web server (`scrimmage edit`): static front end plus a token-protected JSON API on 127.0.0.1.

The app holds one thing at a time (`mode`):
  "robot": a robot config (the Robot tab) and, when the opened file is a scenario, that scenario (the Scenario tab);
        the Drive tab drives the robot document. With a scenario open, the robot document is the scenario's
        `config`: changing the scenario's config (an edit, undo/redo or an outside edit) loads that robot config in
        its place. A block test (`<name>.test.toml`, the Test tab; see test_session.py) attaches the same way: the
        robot document is its [test] `robot`.
  "bench": an idea session, a folder of bench files (the Bench tab; see bench_session.py).
  "part": a CAD import or a plugin-made part folder (the Part tab; see part_session.py).
  "start": nothing (`scrimmage edit` with no FILE): the start page opens or creates something.
classify(): "import" for a .step/.stp/.stl/.obj file, a folder holding a part.toml, or a .toml with a top-level
[import] table (an import file); any other folder is a session; a file is a test when it has a top-level [test]
table, else a scenario when it has a top-level
`config` key or a `[[step]]` array, else a bench file when it has a top-level [bench] table, else a robot config. A
bench file opens its folder as the session, with that file on stage. Opening a CAD file asks for nothing: it makes
parts/<stem>.import.toml next to it (or opens the one there that names it), builds it and opens it. A part folder
with an import-origin.toml marker opens its import file; one without is plugin mode (its scrimmage-edits.toml is
edited). While the app isn't in robot mode the robot, scenario and live routes answer 404 "no robot is open"; while
no session is open the bench routes answer 404 "no idea session is open"; while no part is open the part routes
(but upload) answer 404 "no part is open".

API contract (all bodies JSON; every /api/* request needs header X-Scrimmage-Token and a Host of
127.0.0.1 or localhost, else 403):

App (`/api/app/*`; in every mode):
  GET  /api/app/state              -> {mode: start|robot|bench|part, path, display_path, test, recent: [{path, kind:
        robot|scenario|test|session|import, t}] (newest first, see recent.py), examples: [stems of examples/bench/*.toml],
        needs_folder: "cad"|"code"|null, back: {path, display_path}|null}. back: in part mode, the robot config (or
        scenario) that was open when an import made for it was opened (its import file in that robot's parts/, as the
        library's Import CAD... makes it): "Back to the robot" opens it again; null otherwise. path is the opened
        file (the scenario in scenario mode, the test with a test open), session folder,
        import file or plugin part folder, absolute; null in start mode. needs_folder: the team folder ({cad}, {code}: teamfolders.py) a value in the
        open robot file (or the scenario's) names that this computer hasn't set; the robot is then read-only, its
        read_only_reason saying which folder to set and how. test: the open block test (absolute), else null; opening
        another file closes it.
  GET  /api/app/folders            -> {cad, code, from_env: {cad: bool, code: bool}}: this computer's team folders
        (absolute, null when not set); from_env is true when SCRIMMAGE_CAD / SCRIMMAGE_CODE sets it (it wins over the
        saved one).
  POST /api/app/folders            -> body {name: cad|code, path|null}: remembers (null: forgets) where that folder
        is on this computer (path relative to the working directory, ~ expanded); {ok: true}; 400 for another name
        or a path that isn't an existing folder. The open robot (and scenario) are read again with it.
  GET  /api/app/browse[?dir=D]     -> {dir, parent, entries: [{name, path, kind: folder|robot|scenario|bench|
        import|cad}]}: D's folders, then its .toml files (classified) and .step/.stp/.stl/.obj files (cad), each sorted
        by name; hidden entries and other files left out.
        D defaults to the working directory (relative to it, ~ expanded); parent is null at the root; 400 when D
        isn't a folder.
  POST /api/app/open               -> body {path} (relative to the working directory, ~ expanded): opens a robot
        config, scenario, bench file, import file, CAD file or folder in place of what is open. {ok: true}; 400 when
        it doesn't exist, is a file other than .toml or CAD, or is an import-made part folder whose import file is
        gone; 409 while a drive session is starting, running or paused, a scenario run is in
        progress, or this editor's bench job (below) is queued or running (never because another process, the
        agent or the CLI, holds the session's run lock). Every version keeps counting up across the switch, so
        open long polls return; the page reloads itself after it. Opening remembers the path in the recent list (a bench file as its folder).
  POST /api/app/new                -> body {kind: robot|scenario|session|import, path, template?, robot?}: writes a
        robot config from a template (template: swerve|swerve-rev|differential), a scenario for the robot config
        `robot` (its `config` relative to the scenario's folder) or makes the session folder, then opens it as /open
        does. kind import ("New part from CAD"): path is the Browse-picked source (a CAD file, or a folder of STL/OBJ
        files), referenced in place; its import file goes in parts/ next to it, or in the parts/ folder next to the
        robot config `robot` when given (an existing one naming that source is opened); 400 when path isn't a CAD
        file or such a folder.
        A robot or scenario path without .toml gets it. {ok: true}; 400 for an existing path, a bad kind or
        template, or a `robot` that isn't a robot config; 409 as for open. The templates write no start_pose
        (the robot code panel asks where the robot starts). A scenario's `config` is written as `{cad}/...` (or
        `{code}/...`) when a team folder holds the robot config but not the scenario.

Bench (`/api/bench/*`; 404 unless an idea session is open). `file` is an idea's file name in the session folder:
  GET  /api/bench/state[?since=N]  -> {version, folder, display_folder, stage: file|null, lock: {source, file, pid,
        since}|null, ideas: [{file, name, author, idea, read_only_reason, problems: [{level: error|warning, key,
        message}], focus: [keys], can_undo, can_redo, latest}], result}; long-polls like /api/robot/state. The ideas are
        the folder's *.toml files with a top-level [bench] table, the most recently edited or run first; stage is
        the one edited or run last by anyone (page, agent or CLI), or the next most recent when it is deleted. lock
        is who holds the session's run lock. A file that isn't valid TOML is listed with read_only_reason and its
        TOML error as a problem; it recovers when the file is fixed. Keys are dotted (`wheel.top.diameter`), always
        the meters key even when the file writes `diameter_in`; key null when no field is known. focus: the
        talked-about keys (`.scrimmage-bench/focus.json`), newest first; an edit from the page or outside moves the
        keys it changed to the front. latest: the idea's own result, from the newest session log RUN event naming the
        file (a sweep or compare never replaces it), null when none: {kind: "run", ok, error, t, source, out,
        headlines: [the summary's headline lines, the UNCALIBRATED line last; [] without a summary], uncalibrated:
        always a line starting "UNCALIBRATED", energy_failed: true when the run's summary.txt says the energy check
        failed (the simulation, not the design, is wrong; the same signal whoever ran it; ok stays as logged),
        pictures: {path_side, pass_gif} (absolute paths for /api/bench/file, null when absent)}.
        result: the session's result, from the newest sweep or compare event in the log (any source), null when
        none: {kind: sweep|compare, files: [the idea file names it concerns: the swept idea, or the compared ones
        in order], ok, error, t, source, out, uncalibrated: always a line starting "UNCALIBRATED" (the first idea's
        piece), pictures: [absolute paths: a compare's compare.png; a sweep's charts, the line-*.png in out no
        older than its results.csv; [] when it failed or wrote none], table: {columns: [the swept keys (a
        compare: idea), then out, jammed, exit_speed, elevation_deg, spin_rps, speed_drop_pct, peak_load_n as the
        CSV has them], rows: [[text cells]] (the first 40)} from results.csv / compare.csv, null when it failed or
        has none, on_stage: true while nothing has moved the stage since it (an edit or a run of any idea, by
        anyone, turns it false; after a restart, true when no idea's file or run is newer)}. The page shows the
        result on the stage while on_stage is true, and an idea's latest when it is clicked.
        Outside changes (files, log, focus, lock) show within 0.25 s.
  GET  /api/bench/fields           -> bench.fields.fields_payload(): every field's help, unit and choices.
  GET  /api/bench/view?file=F      -> bench_view of the idea, or {} while it has errors.
  GET  /api/bench/idea?file=F      -> {own: the file's tables as plain values, included: {entry key (bar.kicker,
        shaft.top, piece): the include file it comes from (relative to the folder)}, merged: the bench after
        includes}.
  POST /api/bench/edit             -> body {file, version, op, ...}; ops:
        set      {key, value}                 a dotted key; null removes it; a key ending .name is refused
                                              (rename), except bench.name, the idea's own title
                 {values: {key: value, ...}, field?}   several keys in one edit (one write, one undo step);
                                              field (default the first key) names the edit in a stale refusal,
                                              and errors on its keys are reported under it
        add      {section, name?, fields?}    -> a new entry (named section<N> without a name)
        remove   {section, name}
        rename   {section, name, new_name}    also renames what in the file refers to it
        override {section, name?}             copies an included entry (or [piece]/[run]/[flight]) into the file
        Success {ok: true, version}. Rejected 200 {ok: false, errors: [{field, message}]} (field a dotted key or
        "status"), nothing written. Writing rule: a candidate is written only if it has no more errors than the
        file now has. Stale: when the idea changed after `version` (the session version the page last saw; changes
        to other ideas don't count), 200 {ok: false, version, errors: [{field: "status", message: "the agent
        changed this idea; your edit to <field> wasn't saved"}]} and the file keeps the other edit. 409 when the
        idea is read-only; 404 for an unknown or deleted idea.
  POST /api/bench/undo, /api/bench/redo -> body {file, version}; as edit (nothing to undo: 200 {ok: false}).
  POST /api/bench/new_idea         -> body {name, author?, idea?, example? (a stem from /api/app/state's examples)}:
        a new bench file named from `name` (lower case, made unique), from the blank bench or the example, with
        [bench] name, author and idea set; it goes on stage. {ok: true, file}; 200 {ok: false, errors} for no name
        or an unknown example.
  GET  /api/bench/file?path=P      -> a file under the session folder, or a file the bench writes directly in a
        results folder the session log names (.txt, .csv, .png, .gif: summary.txt, the CSVs, the pictures, the
        sweep and compare outputs) or a logged gallery .html file; P absolute, or relative to the folder; symlinks
        are followed before the check; 404 otherwise. A logged `out` that is the home folder, the filesystem root
        or an ancestor of the session folder is never served from, and a restarted log forgets the old ones. Needs
        the token like every route, so the page fetches pictures into blob: URLs.
  POST /api/bench/run              -> body {file}: runs the idea as `bench run` does (results and pictures in
        <folder>/<stem>-results/). {ok: true, id}; 200 {ok: false, errors: [{field, message}]} when the idea has
        errors or is read-only; 404 for an unknown idea.
  POST /api/bench/sweep            -> body {file, key, values}: runs the idea once per value of `key` (a dotted
        key, checked against the idea) in place of its own [sweep] (its [[chart]]s are dropped; the chart is exit
        speed against `key`); results.csv and the chart in <folder>/<stem>-sweep/. values a non-empty list. The
        file is never changed. {ok: true, id}; 200 {ok: false, errors} (field key or values) as for run.
  POST /api/bench/compare          -> body {files: [two or more different ideas]}: as `bench compare` (the first
        idea's piece and feed); compare.csv and compare.png in <folder>/compare-results/. {ok: true, id}; 200
        {ok: false, errors} as for run.
  POST /api/bench/gallery          -> body {}: as `bench gallery` on the folder; writes <folder>/gallery.html.
        {ok: true, id}.
        Jobs run one at a time, in order, on one worker thread of this editor, each holding the editor's
        simulation lock and the session folder's run lock (shared with the agent and the CLI): while another
        process holds the run lock the job stays queued (the state's lock says who). A finished job is in the
        session log (source "page"), so it reaches the ideas' latest (a run) or the session's result (a sweep
        or compare) the same way an agent's does, before the job shows as done.
  GET  /api/bench/jobs[?since=N]   -> {version, jobs: [{id, kind: run|sweep|compare|gallery, files: [idea file
        names] ([] for a gallery), state: queued|running|done|failed, error, out}]} newest first, the last 10;
        long-polls like the state. failed is only for a job that raised: error is then a plain message (the
        bench's own error: a BenchRunError, a bad sweep value, ...), null otherwise. A run that finished with a
        failed energy check is done; its latest says energy_failed. out is the results folder (the gallery's HTML file). Opening something else forgets the finished jobs.
  A request that obtained the idea session before an open replaced it answers 409 "the idea session was closed".

Part (`/api/part/*`; 404 unless a part is open, except upload). Pieces are keyed as the CAD file's tree spells them
("Climber/hook_assy/hook", "#2" on repeats); points and axes the student picks are CAD coordinates (meters):
  GET  /api/part/state[?since=N]   -> {version, mode: import|plugin, path (the import file or part folder), name,
        source (as the import file stores it; null in plugin mode), source_copied (the source is a dropped or
        chosen file's copy in parts/<name>.source/: Import again asks for the new export), units, frame ({up, front, floor, center, skip,
        confirmed}; null in plugin mode), pieces: [{key, name, group, body, material (null: not chosen yet, built as
        aluminum), infill, custom_density, mass (kg: the typed weight, else volume x density; null when the source
        can't be read), typed_mass, left_out, volume (m^3), hardware (the hardware guess)}], bodies: [{name, parent,
        joint, motor, encoder, gyro, mounts, collision, part, typed_mass, mass (its pieces left in, or its typed
        weight), com (robot coordinates; null for no mass); in plugin mode also changed (the scrimmage-edits.toml
        keys set here: mass, collision, range, spring, damping, frictionloss)}], parts: [{name, folder, mass, com (from the last
        build; null before one), total_mass}], couplings: [{a, b, ratio}] and loops: [{body1, body2, point (CAD)}]
        (as the import file has them; [] in plugin mode), checklist: [{level: todo|check|error, text, target: {kind:
        piece|body|frame, keys}}], messages (what the last edit, undo or re-import dropped or guessed, plain words),
        build: {state: idle|building|failed, message}, can_undo, can_redo, read_only_reason}; long-polls like
        /api/bench/state. Every edit is saved at once; the part folders then rebuild in the background (one build at
        a time, the newest settings win) and the version goes up when the build is done. Outside edits of the import
        file show within 0.25 s (and go on the undo history); a file that stops reading as an import file, or a
        source that can't be read (the cad extra missing: 'Reading CAD files needs the cad extra: pip install
        "scrimmage[cad]"'), makes the part read-only with that reason; the build is then failed with it. In plugin
        mode pieces are [] and bodies come from the folder's part.toml with its overlay (joint: {kind, axis, range,
        spring, damping, frictionloss}, motor and encoder: slot names, mounts: names), and the checklist is the
        overlay's notes.
  GET  /api/part/view              -> {pieces: [{key, vert, face}]}: each piece's mesh (left-out ones too) in robot
        coordinates (base64 little-endian float32 xyz / int32 triangles, as the robot view's meshes); cached per
        source read and robot frame. Plugin mode: one entry per body with meshes (key: the body), in the part frame.
        {pieces: []} while the source can't be read.
  GET  /api/part/materials         -> {materials: {key: {key, label, density, printed}}, motors: [names]}.
  POST /api/part/edit              -> body {version, op, args}: scrimmage.cad.edits.apply_edit(op, args) (set_frame
        leaves the frame unchecked unless args has confirmed = true, which "Looks right" sends; plugin
        mode: set_mass {body, kg}, set_collision {body, mode}, set_joint {body, joint: {range?, spring?, damping?,
        frictionloss?}} (null removes one), set_total {kg}; piece edits are 400 "per-piece materials need the STEP:
        import it instead"). 200 with the state (its messages say what the edit dropped); 400 {field, message} for
        an edit refused (nothing written); 409 when the settings changed after `version` (another tab, an undo, an
        outside edit: a build finishing doesn't count) or the part is read-only.
  POST /api/part/undo, /redo       -> body {version}: a 50-step history of the settings (the build's record of the
        folders it wrote is never undone); 200 with the state ({..., ok: false} when there is nothing to undo).
  POST /api/part/axis              -> body {piece, triangle, kind: round|flat}: cad.axis.fit_axis on the clicked
        triangle (an index into the piece's view faces); {point, axis} in CAD coordinates; 400 with the plain reason
        (a flat face picked as round, a click outside the piece, an unknown piece).
  POST /api/part/reimport          -> body {version?}: reads the source again (the same path) and reconciles the
        settings (pieces keep theirs, matched by name then shape); 200 with the state, its messages saying what
        changed; 400 when it can't be read (the part is then read-only with the reason).
  POST /api/part/upload            -> the raw file as the body, header X-Filename (its name, %-encoded; only the
        last path part is used, and it must end .step/.stp/.stl/.obj): saved into parts/<import name>.source/ next
        to the import file, which then points at it, and re-imported (a dropped file replaces the source). With no
        part open it makes parts/<stem>.import.toml (made unique) in the robot config's folder (robot mode) or the
        working directory, its source in parts/<stem>.source/, and opens it (409 as for /api/app/open). Several
        files at once: header X-Files (a %-encoded JSON list of {name, size}, in the order the body holds them) in
        place of X-Filename; they must be STL or OBJ files (400 in one sentence for STEP files among them), and go
        in parts/<name>.source/meshes/ (replacing it), read together as a folder of them. Header X-Import-Again: 1
        is Import again on a copied source (state.source_copied): the new export keeps the settings as an ordinary
        re-import does. 200 with the state; 400 for a bad name or plugin mode, 413 over 512 MB.

Robot (`/api/robot/*`):
  GET  /api/robot/state[?since=N]  -> {version, path, display_path, read_only, read_only_reason, parts, errors,
        warnings, other_sections, can_undo, can_redo, mass_kg, com, bodies, joints, part_bodies, scenario_open,
        range, start} (range: [range] file as written or null; start: its named start or null). With `since`, returns
        at once if version != N, else long-polls up to 25 s (also noticing outside edits of either file).
        path is absolute; display_path is relative to the server's working directory when the file is under it.
        parts: [{name, stock, path, attach, pos, rot, params, ids, cad_pos, cad}] read from the document, so they are
        listed even when the config is invalid. cad_pos: for a part folder built from a CAD import (it has an
        import-origin.toml), the pos (m, with rot zero) that puts it where the CAD has it on what it attaches to
        (edits.cad_offset), else null. cad: for such a folder {import_id, part, hangs_on}, else null; hangs_on is
        the attach target the CAD hangs it on ("<instance>.<body>" when a part from the same import with that name
        is in the robot, else the marker's "<part>.<body>"), null when the robot itself carries it. mass_kg, com,
        bodies and joints are null when the config is
        invalid or read-only; com: {height, forward, left} (m), the robot's center of gravity at its start pose
        (subtree_com of the robot body in that body's frame: the stock chassis's origin is on the floor at the frame
        center); joints excludes the free joint, bodies excludes the world body (a count).
        part_bodies: {instance: [body names]} (attach targets "<instance>.<body>"), null when read-only/invalid.
        When the scenario names a robot config that can't be read, path is null, parts [] and the robot is
        read-only with the reason. The version keeps counting up when the robot document is replaced.
        scenario_open: true when the opened file is a scenario (the page then shows the Scenario tab). range: the
        config's [range] file as written (null: the plain field).
  GET  /api/robot/view             -> model_view of the current world (com: the robot's CG in the world, for the 3D
        view's CG marker), or {} when read-only/invalid.
  GET  /api/robot/library          -> {stock, folders}; folders lists each part folder (a folder holding part.toml)
        in the config's directory, in its parts/ subfolder, and in any added folders, once each: {name, path,
        slots, mounts, error, cad} (cad: {import_id, part, attach} from its import-origin.toml when it was built
        from a CAD import, else null; attach is the "<part>.<body>" it hangs on in the CAD, or null). Each stock entry
        names its root_body.
  POST /api/robot/edit             -> body {version, op, ...}; ops and their fields:
        add     {name, stock | path, attach?, pos?, rot?, params?, ids?}   without pos, a part folder from a CAD
                import gets its cad_pos (see the state) for that attach: zero on the stock chassis's root body,
                -origins[B] on body B of a part from the same import
        remove  {name}
        rename  {name, new_name}
        attach  {name, attach, pos, rot}
        params  {name, params}   (a null value resets that parameter to its default)
        ids     {name, ids}
        start   {pose?: [x, y, deg] | null, named?: str | null, keep_pose?: bool,
                range?: a built-in range's name ("2026", "ladder", "station", ...) | null, floor_balls?: bool}
                where the robot starts and on which field (the fields but 2026 are the Test tab Setup's).
                range "2026" writes [range] file = "2026" (another range's named start goes), null removes [range]
                (the plain field); absent keeps the config's (one naming another range keeps it). pose writes
                [robot] start_pose (x, y in m from the blue corner, rounded to mm; heading in degrees, (-180, 180])
                and removes [range] start; named writes [range] start and removes start_pose (the loader refuses
                both); a null pose or named clears the start. keep_pose true (the Test tab's field picker) with
                named keeps a start_pose still on the field instead of writing named. floor_balls false writes
                [range] floor_balls = false, true removes it. Field errors: start/range (another range), start/pose
                (not 3 numbers, off the 16.54 x 8.07 m field), start/named (the plain field has none),
                start/floor_balls, start/keep_pose; an unknown named start is the loader's status error.
        battery {nominal_voltage?, resistance?}   [battery] (V, ohm); null removes the key (the default);
                field errors battery/nominal_voltage (a number above 0), battery/resistance (0 or more)
        match_devices {matches: {slot: device}}   points each slot's ID (in its part's `ids`) at a device the
                robot code made: slot as the device check names it ("fl.drive", "fl.drive[1]" for one motor of a
                list), device as its rows give it ("TalonFX 3", "SPARK MAX 5", "ScrimmageMotor:fl_drive"; "TalonFX:3"
                too), always the slot's own device type (the short form's kind is kept: SparkMaxAbs stays an
                absolute encoder). All the IDs change in one write and one undo step, validated once, so two slots
                can swap IDs. Field errors devices/<slot> (no such slot, another device type, a slot naming its own
                `device`, a CAN ID outside 0-62) and devices/matches (empty); a clash with another slot's ID is the
                loader's status error. Success also has restarting: true when a live session on this robot was
                asked to restart to use the new IDs (false otherwise; the restart runs on its own thread, and
                /api/robot/devices' restart says how it went), and restart_problem when it couldn't be asked (its
                recording isn't saved).
        motor   {slot, neutral?: "brake"|"coast"|null, invert?: bool|null}   a motor slot's neutral mode and invert,
                written into its `ids` entry as hand-written configs do (`drive = { ctre = "TalonFX:1", neutral =
                "brake" }`): a short ID becomes that table, and a table left with only its device goes back to the
                short ID. neutral null removes it (the device's default: coast for a TalonFX, else brake); invert
                true flips the motor from the part's own direction, false or null removes it. Field error
                motor/<slot> (a bad value, not a motor, no such slot).
        Success: {ok: true, version}. A rejected edit (field errors or validation): 200 with
        {ok: false, errors: [{field, message}]} and nothing is written. An add's part `path` outside the config's
        folder but in a team folder is written as `{cad}/...` / `{code}/...`.
  POST /api/robot/undo, /api/robot/redo  -> body {version}; {ok: true, version}, or 200 {ok: false, errors} when
        there is nothing to undo/redo or the restored text would no longer validate (nothing is written).
  POST /api/robot/folder           -> body {path}; adds a parts folder to the library. A relative path resolves
        against the config's directory (~ is expanded); it must be an existing directory (else 400).
        Returns {ok: true}.
  GET  /api/robot/code             -> {folder, display_folder, cwd, problem, info, checks, start, battery}: the
        robot code panel. cwd is [robot_program] cwd as written (null without a [robot_program]); folder the
        absolute folder it names (display_folder relative to the working directory when under it), null when
        there is no program or a team folder isn't set. problem says why the code can't be used (an unset team
        folder, a folder that isn't there, a folder with no robot project: robotcode.detect's summary), else null.
        info: robotcode.ProjectInfo {folder, kind: java|robotpy, vendors, tuner_swerve, python, summary} or null;
        checks: robotcode.checklist [{id, ok: true|false|null (information), message, fix: build_agent|
        gradle_build|install_lockstep|null, detail}] ([] without info; they run programs, so this takes a moment).
        gradle_built is ok when the folder has a jar in build/libs or compiled classes under build/classes (a
        build/ folder alone isn't: a failed build leaves one); while this editor's last Build now of the folder
        failed and nothing has built there since, it is that failed result (its message and detail).
        start: {picked, pose: [x, y, deg] (a named start's pose from its range) | null, range: the [range] file |
        null (the plain field), named: [range] start | null, named_starts: [names of the range's starts, in its
        order], floor_balls, fields: ["2026", null] (the picker's choices), range_read_only: true for a range the
        picker doesn't offer (shown by name), sentence: plain words ("Starts on the blue side, facing the red
        wall": the half of the field x is in, the wall within 45 deg of the heading, else "a side wall"; "Not
        picked yet")}. picked: the file has [robot] start_pose or [range] start. battery: {nominal_voltage,
        resistance (as written, null when left out), defaults: {nominal_voltage: 12.0, resistance: 0.02}}.
  POST /api/robot/code             -> body {folder, version?}: detects the robot project there (relative to the
        working directory, ~ expanded) and writes [robot_program] command and cwd (robotcode.program_table; its
        other keys stay; cwd as `{code}/...` when a team folder holds it but not the robot file, else relative) as
        one undoable edit. {ok: true, version, info}; 400 {ok: false, errors} with the plain summary when nothing
        there can start, or when it isn't a folder (nothing written); 409 when the robot is read-only; otherwise
        as an edit.
  POST /api/robot/code/fix         -> body {fix: build_agent|gradle_build|install_lockstep}: runs robotcode.run_fix
        for the robot's code on the fix worker, one at a time. {ok: true, id}; 400 for another fix or no usable
        robot code; 409 while a fix is running. Closing the editor stops a running fix's program.
  GET  /api/robot/code/fix[?since=N] -> {version, id, fix, running, lines: [its output lines, the last 1000],
        result: the re-checked item (a check as above) | null while running}; long-polls like the state.
  POST /api/robot/check            -> body {speed?, discard_recording?}: Start and check devices. Starts the robot
        document's program as /api/live/start does, refused the same ways (field start/unpicked while no start is
        picked, start/not_built, live/unsaved, 409 while a session is live or a scenario run is in progress); it runs disabled
        (until someone enables it in Drive), and keeps running: the devices fill in as the code makes them.
  GET  /api/robot/devices[?since=N] -> {version, live, robot_time, settled, rows: [devicecheck.DeviceRow {slot,
        kind, expected, made, status: ok|waiting|missing|extra|other_type|not_checked, suggest: [devices], note}]}:
        the latest drive session's device check (started by /api/robot/check or Drive's Start), updated while it
        runs; the rows stay after it ends ([] before any). live: a session is starting, running or paused.
        robot_time: seconds since the robot connected (a device not made by MISSING_AFTER = 2 s is "missing", not
        "waiting"); settled: robot_time has reached 3 s, when every device made at start-up is in. restart: null,
        or how match_devices' restart went: "restarting" (asked), "restarted" (the new session started; it stays
        until the next start of its own), "failed" (restart_problem says why; else restart_problem is null). The
        version changes with the rows, settled, live or restart (not the clock alone); with `since` it long-polls
        up to 25 s.
Scenario (`/api/scenario/*`; 404 while no scenario is open, except /api/scenario/open):
  GET  /api/scenario/state[?since=N] -> {version, path, display_path, read_only, read_only_reason, robot_problem,
        top: {config, description, speed, require_lockstep}, steps: [{name, mode, duration, start_t, axes, buttons,
        povs, checks: [{kind, spec}], shots: [{view?, at?, width?, height?, camera?}], sheet}], errors, warnings,
        can_undo, can_redo}; each step also has inputs: the track CSV's path as written, or null. Long-polls like /api/robot/state.
        top and steps are read from the document as written (null / {} / [] where a key is absent), so they are
        listed even when the scenario is invalid. start_t is the step's start time (the sum of the earlier
        durations). axes/buttons/povs: {"<port>": [values]}. A check with other than exactly one key has kind null
        and the whole table as spec. sheet: the [step.contact_sheet] table or null.
        robot_problem: why the scenario's robot config can't be loaded (missing or invalid), else null; the
        scenario is then read-only with read_only_reason "fix the robot first: ..." (also when the robot config is
        deleted while open: "the robot config <path> was deleted"; it recovers when the file is back). Unparsable
        TOML is read-only too, and so is the Robot tab then (the scenario's robot config isn't known).
        Writing rule (edit, undo, redo): while the file on disk is valid, a candidate is written only if it is
        valid; while it is invalid (its robot fine), a candidate is written if it has no more errors than the file
        now has (errors counts every step's errors: each step's load errors stop at its first, as the loader's do),
        so fixing one error at a time works even when fixing one reveals the next. Nothing is written
        while the robot has a problem. Run is refused until the file is valid.
        The version also goes up when the robot changing alters the scenario's errors or read-only state.
  GET  /api/scenario/track?step=i  -> {path, duration, t: [...], series: {"p0.axis1": [...], ...}}: step i's input
        track as its CSV has it (one series per column; buttons 0/1); 400 for a bad step, 404 when the step has
        no `inputs` or the file doesn't load.
  POST /api/scenario/edit          -> body {version, op, ...}; steps, checks and screenshots are 0-based indices:
        top             {key, value}               config / description / speed / require_lockstep; null removes
                                                   (changing config loads that robot config as the robot document)
        add_step        {after?, name?, mode, duration}   after null/absent appends; an empty name leaves it out
        duplicate_step  {step}
        delete_step     {step}
        move_step       {step, to}
        set_step        {step, fields: {name?, mode?, duration?}}   a null name removes it
        inputs_file     {step, path}               inputs = "<file>.csv" (drops the step's axes/buttons/povs);
                                                   null path removes it
        trim_track      {step, start, end}         cut the step's track to start..end (0 <= start < end <=
                                                   duration) into a NEW <stem>.trim<N>.csv next to it, point
                                                   inputs there, duration = end - start; the old file stays
                                                   (undo brings it back); a rejected edit removes the new file
        inputs          {step, kind: axes|buttons|povs, port: 0-5, values: [...]}   null or [] removes the port
        add_check       {step, kind, spec}         spec: the check's value (a table, or text for *_touch)
        set_check       {step, check, spec: {kind: value}}
        delete_check    {step, check}
        add_shot        {step, spec: {view?, at?, width?, height?, camera?}}
        set_shot        {step, shot, spec}         merges; a null value removes that field
        delete_shot     {step, shot}
        sheet           {step, spec: {frames?, view?, width?, height?} | null}   merges; null spec removes the sheet
        Success {ok: true, version}; rejected 200 {ok: false, errors} (nothing written), as for the robot.
        While a drive session is live, an edit that changes config away from the session's robot config is 409
        "a drive session is live" (an outside edit of the file can't be refused: the robot document follows it).
  POST /api/scenario/undo, /api/scenario/redo -> as the robot's; refused like an edit when restoring would change
        config away from a live session's robot config.
  GET  /api/scenario/signals       -> signals(world) for the robot ({fixed, motors, joints, contacts, cameras}), or
        {} when the robot config doesn't load.
  GET  /api/scenario/configs       -> {configs: [path, ...]}: robot configs for the header's dropdown, each as the
        scenario's `config` value would name it (relative to the scenario's folder, "/"-separated; absolute when
        no relative path exists): the *.toml files in configs/ under the server's working directory, then those
        next to the scenario that aren't scenarios themselves; sorted within each folder, each file once.
  GET  /api/scenario/view          -> model_view(world, include_world=True) (robot geoms plus world_geoms for the
        field picker; also for hand-written robots), or {} when the robot config doesn't load.
  POST /api/scenario/run           -> body {version, speed?}: runs a snapshot of the scenario as it is now (speed
        overrides the scenario's: a number or "max"). {ok: true, id}; 200 {ok: false, errors} when the scenario
        has any errors or the speed is bad, with field "start/not_built" as /api/live/start's while the robot's Java
        code isn't built (checked first), or with field "start/unpicked" as /api/live/start's while the robot
        has no start and the scenario's [range] gives none; 409 when a run is already in progress (or a stale
        version).
  POST /api/scenario/stop          -> {ok: true}; asks the current run (if any) to stop, also while its robot
        program is starting (the robot program is killed). Closing the editor stops a run the same way.
  GET  /api/scenario/runs[?since=N] -> {version, runs: [{id, state: running|passed|failed|error|stopped, step,
        step_name, t, total_t, console, report, out_dir}]} newest first (this session's last 10). Long-polls like
        the state routes.
  GET  /api/scenario/run/<id>/<file> -> a file from that run's folder (report.md, report.json, screenshots, ...);
        404 for anything outside it or an unknown run. Like every /api/* route it needs the X-Scrimmage-Token header
        (never a query parameter), so the page fetches images into blob: URLs and shows text files as text.

  POST /api/scenario/open          -> body {path}: opens that scenario as the scenario document, as if it had been
        given to `scrimmage edit` (path relative to the server's working directory, ~ expanded); the robot
        document follows its config, and /api/robot/state then says scenario_open. {ok: true}; 400 when it isn't a
        scenario file; 409 while a run is in progress ("a scenario run is in progress"). While a drive session
        is starting, running or paused, 409 "a drive session is live" unless the scenario's config is the live
        session's robot config (compared as resolved paths, or the same file): a scenario naming another config,
        none, with a TOML error, or naming a file that can't be read is refused (a recording just saved from that
        session opens). Works when the editor was opened on a robot config too.

Test (`/api/test/*`; 404 "no test is open" unless a test is open, except /api/test/new). See test_session.py:
  GET  /api/test/state[?since=N]   -> {version, path, test: the test as JSON (scrimmage/blocks/program.py to_json),
        palette (scrimmage/blocks/palette.py; null when the robot doesn't load), problems: [{place, message}] (a
        robot that doesn't load is one problem at place []), running: null|{kind: run|sweep|spread, progress: 0..1},
        runs: list_runs (newest first; a sweep's runs have ids "sweep-NNN/NNN", a spread's "spread-NNN/NNN";
        cancelled: Stop ended it), sweeps: [{id, target, values, cancelled, problems, rows}] (newest first), spreads:
        list_spreads ([{id, spread, placements, picked, sentence, cancelled, rows}], newest first), file_error: why
        the file as edited outside can't be read (the last good test is kept) or null, run_error: why the last run,
        sweep or spread couldn't run or null, last: {kind, id} of the last run, sweep or spread this editor made, or
        null, fields: test_session.field_choices() (the plain field {file: null, name, starts: []}, then every
        built-in range: {file, name, starts (file order), disc, ball_choice, stations}), station_starter: the starter
        feed blocks for the robot's field (start feeding from its first station, wait until 5 are in the hopper
        within 20 s, stop feeding) as JSON, or null on a field with no station, starter_hopper: the bodies Setup
        ticks as the hopper of a test with none (test_session.starter_hopper; [] without one)}; long-polls like /api/robot/state.
        The robot changing on disk (an edit, Import again, a part folder's files) shows in the next state's palette
        and problems.
  POST /api/test/save              -> body {test, version}: replaces the test and writes the file. {ok, version};
        400 with the block's place for a bad shape; 409 {error, state} when the test changed after `version`.
  POST /api/test/run               -> {ok: true} once the run starts on its thread; 409 {error, problems} while there
        are problems; 409 "Wait for the drive session to stop first." (and so on) while another simulation runs;
        409 while a test run is in progress or the file can't be read. The run is kept when it ends.
  POST /api/test/sweep             -> body {target, values} (scrimmage/blocks/sweep.py); refused as run; 400 for a
        target or values it can't sweep.
  POST /api/test/spread            -> runs the test once per placement of its [setup] floor_spread
        (scrimmage/blocks/spread.py) on thread "test-spread"; refused as run, and 409 "the floor spread needs a ball
        on the floor in front of the robot's start" when the field has none there (palette.floor_piece), 409 when
        the test has no floor spread, or 409 (spread.inside_robot) when a placement puts the ball inside the robot.
        The spread is kept (the newest 3) when it ends.
  POST /api/test/cancel            -> {ok: true}; stops the run, sweep or spread under way (a cancelled run is kept).
  GET  /api/test/run/<id>          -> (<id>: "004", "sweep-002/003", "spread-001/002") result.json's fields plus id
        and signals; /view -> view.json; /motion -> motion.bin (application/octet-stream); 404 for a run that isn't
        kept.
  POST /api/test/new               -> body {robot?, from_part?} (default: the open robot): writes <robot
        stem>.test.toml next to it (<stem>-2.test.toml ... when taken) and opens it as /api/app/open does. Its
        blocks are the starter blocks
        (test_session.starter_blocks: on a field with a station, its station_starter; else drive 0.3 m when the robot
        is a tank drive and the field has bars, then a lone hinge motor moved to 45 deg, or the first motor spun at 6 V
        for 2 s and all stopped; else wait 1 s), its hooks the robot's bodies named like a hook and its hopper those
        named like a hopper ("hopper", "intake" or "indexer"). With from_part: true and a part from a CAD import open
        ("Test it" in the Part tab): makes <import name>-standin.toml (the standin template carrying the built parts
        where the CAD has them, in front of the ladder range when a body is named like a hook, else at the station
        range's chute when one is named like a hopper) and <import name>.test.toml next
        to the import's parts/ folder, once each, and opens the test. Pressed again it opens the same test and adds
        the import's built parts the stand-in doesn't have yet, where the CAD has them (it never removes one);
        {ok, added: [their names]}. 409 while the parts are updating, didn't build or the part is read-only, 400
        with no part open or in plugin mode.

Live (`/api/live/*`; one simulation at a time: a drive session, a scenario run, a bench job or a test run):
  GET  /api/live/info              -> {port, status, run_active, can_start, start_problem}: port is the stream's
        WebSocket port; status is the session's {state: idle|starting|running|paused|stopped|error, error, config,
        t, mode, chosen_mode, enabled, paused, speed, speed_locked, recording, pokes, can_save} (mode is the
        effective mode, "disabled" while not enabled; chosen_mode is the one Enable runs); run_active is true while a
        scenario run is in progress; can_start is false when the robot document can't be driven (it doesn't load
        or has no [robot_program]), with start_problem saying why in Start's own words (else null). start_picked
        is false while a [[part]] robot has neither [robot] start_pose nor [range] start (Start then asks).
  GET  /api/live/view              -> model_view(world, include_world=True) of the robot document (movers and
        movable included; frames list poses in its movers order), or {} when the robot config doesn't load.
  GET  /api/live/signals           -> signals(world) of the robot document, or {}.
  POST /api/live/start             -> body {speed?, discard_recording?} (speed: a positive number, default 1, or
        "max"): drives the robot document's config. {ok: true}; 200 {ok: false, errors: [{field: "status",
        message}]} when the robot config doesn't load, has no [robot_program] or the speed is bad; 200 {ok: false,
        errors: [{field: "start/unpicked", message: "Pick where the robot starts first"}]} while start_picked is
        false (only the page asks: the CLI, the agent and the loader keep their default start); 200 {ok: false,
        errors: [{field: "start/not_built", message}]} (checked before start/unpicked) when the robot program's
        folder is a Java project that hasn't built on this computer (robotcode.built_at), whose last Build now
        failed (as /api/robot/code's gradle_built), or while Build now is running: the message says so in plain
        words (only the page's starts: the CLI's drive and run don't ask); 200 {ok: false,
        errors: [{field: "live/unsaved", message}]} when the last session left a recording that was never saved
        (the page asks, then starts again with discard_recording: true, which drops it); a recording counts as
        saved after a successful save_recording, until the next record_start. 400 when discard_recording isn't
        true or false; 409 while a scenario run is in progress ("a scenario run is in progress") or a session is
        already live. Starting a run while live is 409 too.
  POST /api/live/stop              -> {ok: true}; ends the session (also during the robot program's start-up).
        Closing the editor ends it the same way.
  POST /api/live/save_recording    -> body {path, overwrite?}: writes the recording as a scenario (NAME.toml and
        its NAME/ track folder); path relative to the server's working directory, ~ expanded. {ok: true, path,
        display_path, steps, pokes}; 200 {ok: false, errors} when the path is under neither the working directory
        nor the robot config's folder, nothing was recorded or it can't be written; a file or folder that already
        exists (without overwrite: true) is refused with field "save/exists". Overwrite replaces only an earlier
        recording, a NAME.toml whose first line is the recorder's "# Recorded in the Drive tab" comment: with or without
        overwrite, field "status" refuses a NAME.toml without that line (a hand-written scenario, a robot config), a
        NAME/ folder without such a NAME.toml, and a NAME/ folder holding anything but step<k>.csv files (and
        Trim's step<k>.trim<n>.csv copies). The old recording is moved aside and deleted only once the new one is in
        place; if it can't be put back after a failed save, the error names the folder it is kept in.

Stream (WebSocket on 127.0.0.1:<port from /api/live/info>): the handshake's Host must be 127.0.0.1 or localhost
with that port and its Origin the page's own (http://127.0.0.1:<http port> or http://localhost:<http port>), and
the first message must be {"token": "<session token>"} within 5 s; otherwise the socket is closed (code 1008) with
nothing sent. Then the server sends:
  binary frames, little-endian packed: u32 seq, u8 source (0 live, 1 run), f64 t, u16 n, then n x 7 float32
        (x, y, z, qw, qx, qy, qz) per mover in the view's movers order; at most one per 33 ms per source.
  {"type": "state", "source": "live", ...status, readouts, signals, console (last 200 lines), console_seq} and,
        during a scenario run, {"type": "state", "source": "run", t, step_name, mode, range (the score counts or null)}; at most every 100 ms, each a
        full snapshot (latest wins: a slow page may miss intermediate ones).
  {"type": "notice", kind, text}: e.g. a refused command (queued; none are dropped while the page keeps up).
The page sends {"type": "input", "ports": {"<port>": {axes, buttons, povs}}} and {"type": "cmd", "cmd": ...}
(mode, enable, disable, pause, resume, step, speed, set_pose, push, move_body, reset, record_start, record_stop,
signals); a bad command comes back as an error notice, malformed messages (JSON nested too deeply to parse among
them) are ignored; before the token, a malformed first message is refused like a wrong token. When the last page's
socket closes, every port is zeroed and the robot disabled and paused until a page reconnects.

`version` is required on edit/undo/redo/run (400 if missing); a mismatch is 409 and the page's next
state shows the reloaded document.

Status codes: 200 ok (including rejected edits), 400 malformed request or unknown op/endpoint body,
403 bad token or Host, 404 unknown endpoint or file, 409 read-only document, stale version/changed file or a run in
progress, 413 body over 1 MB, 500 unexpected failure. Every non-200 JSON body is
{ok: false, error: <message>, errors: [{field: "status", message}]}; non-field errors use field "status".
Robot field errors use "<instance>/<key>" (key: name, attach, pos, rot, param:<name>, ids:<slot>, part); scenario
field errors use "scenario/<key>", "step<i>/<key>", "step<i>/check<j>/<key>", "step<i>/shot<j>/<key>" and
"step<i>/sheet/<key>" (0-based).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
import secrets
import threading
import time
import tomllib
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import mujoco
import tomlkit
from tomlkit.exceptions import TOMLKitError

from .. import robotcode, teamfolders
from ..config import ConfigError, load_config_text
from ..lockstep import parse_speed
from ..parts.model import PartError, load_part
from ..parts.stock import REGISTRY, _load_all
from ..scenario import ScenarioError
from ..scenario_runner import UNSET
from ..testrange.spec import BUILTIN_RANGES, RangeError, load_range
from ..tracks import TrackError, load_track
from ..cad import CadError
from ..cad.build import part_folder
from ..cad.names import sanitize
from . import (bench_session, edits, library, part_session, recent, scenario_edits, scenario_validate, templates,
               test_session, validate)
from .bench_session import BenchJobs, BenchSession, SessionError
from .doc import ConfigDoc, FileChanged
from .edits import EditError
from .live import FRAME_HEAD, LiveError, LiveManager, UnsavedRecording
from .recorder import RecordingError
from .runs import RunBusy, RunManager
from .signals import signals
from .simlock import SimBusy, SimLock
from .stream import StreamHub
from .test_session import TestSession
from .view import model_view
from ..blocks.program import Program as TestProgram, TestFileError, dumps as dumps_test

STATIC = Path(__file__).resolve().parent / "static"
POLL_SECONDS = 25.0
MAX_BODY = 1_000_000
HAND_WRITTEN = "this robot uses a hand-written model; the assembler edits [[part]] robots"
NO_PROGRAM = "this robot config has no [robot_program] section: add one to drive it"
NO_ROBOT = "no robot is open"
NO_SESSION = "no idea session is open"
NO_PART = "no part is open"
NO_TEST = "no test is open"
ROBOT_TEMPLATES = ("swerve", "swerve-rev", "differential")
UNPICKED = "Pick where the robot starts first"
NOT_BUILT = ("The robot code hasn't been built on this computer yet. Build it first (Build now, under Ready to "
             "start? in the Robot code panel): the first build downloads the libraries, so it takes a few minutes.")
NOT_BUILT_FAILED = ("The robot code didn't build the last time. Fix what the build said (under Ready to start? in "
                    "the Robot code panel), then build it again.")
NOT_BUILT_RUNNING = "The robot code is still building. Wait for the build to finish, then start again."
NOT_PICKED = "Not picked yet"
FIXES = ("build_agent", "gradle_build", "install_lockstep")
FIX_LINES = 1000          # a fix's output lines kept for the page (the last ones)
BATTERY_DEFAULTS = {"nominal_voltage": 12.0, "resistance": 0.02}   # world.py's, when [battery] leaves one out
_FOLDER_PLACEHOLDER = re.compile(r"\{(cad|code)\}")
SOURCE_RUN = 1
FRAME_PERIOD = 1 / 30     # at most one frame per 33 ms of wall time per source
STATE_PERIOD = 0.1
_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml",
          ".png": "image/png", ".txt": "text/plain; charset=utf-8", ".map": "application/json",
          ".md": "text/markdown; charset=utf-8", ".csv": "text/csv; charset=utf-8",
          ".log": "text/plain; charset=utf-8", ".toml": "text/plain; charset=utf-8", ".gif": "image/gif",
          ".cur": "image/x-icon"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _plain(v):
    return v.unwrap() if hasattr(v, "unwrap") else v


def _chassis_root_body() -> str:
    """The stock chassis's root body ("chassis"): an import part attached to it at zero offset sits where the CAD has it."""
    _load_all()
    return library.root_body("chassis")


def _stock_defaults(stock) -> dict:
    _load_all()
    s = REGISTRY.get(stock) if isinstance(stock, str) else None
    return {p.name: (list(p.default) if p.kind == "pair" else p.default) for p in s.params} if s else {}


def _fe(errs) -> list[dict]:
    return [{"field": e.field, "message": e.message} for e in errs]


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


_STEP_HEADER = re.compile(r"(?m)^[ \t]*\[\[[ \t]*step[ \t]*\]\]")
_CONFIG_KEY = re.compile(r"(?m)^[ \t]*config[ \t]*=")
_TABLE = re.compile(r"(?m)^[ \t]*\[")
_BENCH_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*bench[ \t]*\]")


def _strings(v):
    """Every string in a parsed TOML value."""
    if isinstance(v, str):
        yield v
    elif isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)


def unset_folder(values) -> tuple[str, str] | None:
    """(team folder name, the plain message) for the first `{cad}`/`{code}` in `values` (a parsed TOML value) that
    this computer hasn't set, else None."""
    known = teamfolders.folders()
    for text in _strings(values):
        for m in _FOLDER_PLACEHOLDER.finditer(text):
            if known[m.group(1)] is None:
                try:
                    teamfolders.expand(m.group(0))
                except teamfolders.TeamFolderError as e:
                    return m.group(1), str(e)
    return None


def start_sentence(pose) -> str:
    """Plain words for a start pose (x, y, heading in degrees; the blue alliance wall at x = 0, red at the far end):
    which alliance's half of the field it is on, and which wall it faces."""
    x, _, h = (float(a) for a in pose)
    side = "blue" if x < edits.FIELD_LENGTH / 2 else "red"
    h = (h + 180.0) % 360.0 - 180.0
    facing = "the red wall" if abs(h) <= 45 else "the blue wall" if abs(h) >= 135 else "a side wall"
    return f"Starts on the {side} side, facing {facing}"


_IMPORT_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*import[ \t]*\]")
_TEST_HEADER = re.compile(r"(?m)^[ \t]*\[[ \t]*test[ \t]*\]")


def classify(path) -> str:
    """"import" for a CAD file (.step/.stp/.stl/.obj), a folder holding a part.toml, or a file with a top-level
    [import] table; "session" for another folder (of bench files); for a file, "test" when it has a top-level [test]
    table (a block test), else "scenario" when it has a top-level `config` key or a [[step]] array, else "bench"
    when it has a top-level [bench] table, else "robot". A file that isn't valid TOML is judged by its lines (an
    [import] header; a [test] header; a [[step]] header or `config =` before the first table; then a [bench]
    header)."""
    if part_session.is_part_target(path):
        return "import"
    if Path(path).is_dir():
        return "session"
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "robot"
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        if _IMPORT_HEADER.search(text):
            return "import"
        if _TEST_HEADER.search(text):
            return "test"
        first_table = _TABLE.search(text)
        head = text[:first_table.start()] if first_table else text
        if _STEP_HEADER.search(text) or _CONFIG_KEY.search(head):
            return "scenario"
        return "bench" if _BENCH_HEADER.search(text) else "robot"
    if isinstance(raw.get("import"), dict):
        return "import"
    if isinstance(raw.get("test"), dict):
        return "test"
    if "config" in raw or "step" in raw:
        return "scenario"
    return "bench" if isinstance(raw.get("bench"), dict) else "robot"


def _info_json(info) -> dict:
    """A robotcode.ProjectInfo as JSON (paths as text)."""
    d = dataclasses.asdict(info)
    d["folder"] = str(info.folder)
    d["python"] = str(info.python) if info.python is not None else None
    return d


def remember_opened(path: Path) -> None:
    """Put an opened file in the start page's recent list: a robot, a scenario, a test, a session (a bench file's
    folder) or an import (a CAD file's import file, an import-made folder's, or a plugin part folder)."""
    kind = classify(path)
    if kind == "import":
        return   # App.remember_part does, once the import file is known
    recent.remember(Path(path).parent if kind == "bench" else path, "session" if kind in ("bench", "session") else kind)


class App:
    def __init__(self, path: Path | None, open_browser: bool = True, port: int = 0,
                 runs_dir: Path | str | None = None, parts_dir: Path | None = None):
        self.cwd = Path.cwd().resolve()
        self.open_browser = open_browser
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.extra_folders: list[Path] = []
        self.robot: ConfigDoc | None = None
        self.robot_missing: str | None = None     # why there is no robot document (scenario mode only)
        self._robot_target: Path | None = None    # the robot config the scenario names
        self._robot_ver = 0                       # the robot version while there is no robot document
        self.result: validate.Result | None = None
        self.scenario: ConfigDoc | None = None
        self.scenario_result: scenario_validate.Result | None = None
        self.scenario_read_only_reason: str | None = None
        self.scenario_robot_problem: str | None = None
        self.scenario_errors: list[dict] = []
        self.test: TestSession | None = None      # the block test (robot mode; its robot is the robot document)
        self.bench: BenchSession | None = None    # the idea session (bench mode)
        self.part = None                          # the PartSession or PluginSession (part mode)
        self.back: Path | None = None             # part mode: the robot config (or scenario) the part was opened from
        self._back_parts: Path | None = None      # ... and that robot's parts/ folder, where its imports go
        self._com: tuple | None = None            # (world, its CG) for the robot state
        self._view: dict | None = None
        self._scenario_view: dict | None = None
        self._signals: dict | None = None
        self._parts_seen = None                   # _parts_look() when the robot was last validated
        self._part_meshes: dict = {}              # (part.toml, its stat) -> the mesh files it names
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        # run progress has its own condition: the run's worker never waits for the app lock (edit validation)
        self._runs_cond = threading.Condition()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        # one simulation at a time: a drive session or a scenario run; both stream to the page through the hub
        self.simlock = SimLock()
        self.hub = StreamHub(self.token, lambda: self.port, self._live_message, self._live_client_gone)
        self.live = LiveManager(self.simlock, self.hub.publish, frame_period=FRAME_PERIOD, state_period=STATE_PERIOD)
        self._watch_frame = self._watch_state = -math.inf
        self._watch_seq = 0
        # runs go where `scrimmage run` puts them: runs/ in the working directory
        self.runs = RunManager(self._runs_changed, runs_dir=Path(runs_dir) if runs_dir else self.cwd / "runs",
                               simlock=self.simlock, watch=self._watch_run)
        # the Bench tab's runs, sweeps, compares and galleries: one worker thread, jobs in order
        self.bench_jobs = BenchJobs(self.simlock, lambda: self.bench)
        # the robot code panel's fixes (a Gradle build, ...): one at a time on one worker thread, never under _lock
        self._fix_cond = threading.Condition()
        self._fix = {"version": 0, "id": 0, "fix": None, "running": False, "lines": [], "result": None}
        self._fix_thread: threading.Thread | None = None
        self._fix_cancel = threading.Event()
        self._build_failed: dict[str, tuple[float, dict]] = {}   # code folder -> (when, the failed gradle_built check)
        self._ranges: dict = {}   # (range file, its mtime) -> its named starts
        with self._lock:
            self._load(Path(path) if path is not None else None, initial=True, parts_dir=parts_dir)

    # -- what is open -----------------------------------------------------------------------
    @property
    def mode(self) -> str:
        """"bench" (an idea session), "part" (a CAD import or plugin part folder), "robot" (a robot config, a
        scenario or a test) or "start" (nothing)."""
        if self.bench is not None:
            return "bench"
        if self.part is not None:
            return "part"
        return "robot" if self.robot is not None or self.scenario is not None or self.test is not None else "start"

    def _load(self, path: Path | None, initial: bool = False, kind: str | None = None,
              parts_dir: Path | None = None, source: Path | None = None) -> None:
        """Make `path` (a robot config, scenario, bench file, folder, import file, CAD file or part folder; None:
        nothing) what the app holds, replacing what it held. kind "import" opens `path` as a part even when classify
        wouldn't (a folder of STL files), its import file made in `parts_dir` (default parts/ next to it); with
        `source`, `path` is the import file to make from that CAD file. Every
        version keeps counting up, so open pages' long polls return. Called with self._lock held; OSError when the
        file can't be read, CadError when an import-made folder's import file is gone (nothing is replaced then)."""
        kind = kind or (classify(path) if path is not None else None)
        floor = 0 if initial else 1 + max(self.robot_version, self.scenario.version if self.scenario else 0,
                                          self.bench.version if self.bench else 0,
                                          self.part.version if self.part else 0,
                                          self.test.version if self.test else 0)
        doc = ConfigDoc(path) if kind in ("robot", "scenario") else None
        test = TestSession(path, self.simlock, floor) if kind == "test" else None   # TestFileError: nothing replaced
        # "Back to the robot": an import made for the open robot (its parts go in that robot's parts/, as Import CAD...
        # does) leads back to it; another import opened from the part leads back too while it is made for that robot
        back = robot_parts = None
        if kind == "import" and self.mode == "robot" and (self.scenario or self.robot) is not None:
            back, robot_parts = (self.scenario or self.robot).path, (self.robot or self.scenario).path.parent / "parts"
        elif kind == "import" and self.mode == "part" and self.back is not None:
            back, robot_parts = self.back, self._back_parts
        bench = part = None
        if kind in ("bench", "session"):
            folder, stage = (path, None) if kind == "session" else (path.parent, path.name)
            bench = BenchSession(folder, lambda: None, stage=stage, version=floor, cwd=self.cwd)
        elif kind == "import":
            part = part_session.open_part(path, floor, parts_dir, source)
        old, self.bench = self.bench, bench
        old_part, self.part = self.part, part
        if robot_parts is not None and (part is None or Path(part.path).parent.resolve() != robot_parts.resolve()):
            back = robot_parts = None
        self.back, self._back_parts = back, robot_parts
        self.robot, self.robot_missing, self._robot_target, self._robot_ver = None, NO_ROBOT, None, floor
        self.scenario, self.extra_folders = None, []
        old_test, self.test = self.test, test
        if kind == "robot":
            doc.version = floor
            self.robot, self.robot_missing = doc, None
        elif kind == "scenario":
            doc.version = floor
            self.scenario = doc
            self._point_robot(initial=initial)
        elif kind == "test":
            self._point_test_robot(initial=initial)
        self._revalidate()
        self._revalidate_scenario()
        if old is not None:
            old.close()
        if old_part is not None:
            old_part.close()
        if old_test is not None:
            old_test.close()

    def need_robot(self) -> None:
        """404 unless a robot config or scenario is open (the Robot, Scenario and Drive tabs' routes)."""
        with self._lock:
            if self.mode != "robot":
                raise ApiError(404, NO_ROBOT)

    def need_test(self) -> TestSession:
        with self._lock:
            if self.test is None:
                raise ApiError(404, NO_TEST)
            return self.test

    def need_bench(self) -> BenchSession:
        with self._lock:
            if self.bench is None:
                raise ApiError(404, NO_SESSION)
            return self.bench

    def need_part(self):
        with self._lock:
            if self.part is None:
                raise ApiError(404, NO_PART)
            return self.part

    def remember_part(self) -> None:
        """The open part in the recent list: its import file, or the plugin part folder."""
        with self._lock:
            p = self.part.path if self.part is not None else None
        if p is not None:
            recent.remember(p, "import")

    def _user_path(self, path, what: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, f"path must be {what}")
        p = Path(path).expanduser()
        return (p if p.is_absolute() else self.cwd / p).resolve()

    def _check_can_open(self) -> None:
        """409 while a drive session or a scenario run is live: they keep the robot document they started with."""
        if self.live.status()["state"] in ("starting", "running", "paused"):
            raise ApiError(409, "a drive session is live; stop it first")
        if self.runs.running:
            raise ApiError(409, "a scenario run is in progress; stop it first")
        if self.bench_jobs.active:   # this App's own job; another process holding the session lock never refuses
            raise ApiError(409, "a bench run is in progress; wait for it to finish")
        if self.test is not None and self.test.busy:
            raise ApiError(409, "a test run is in progress; stop it first")

    def app_state(self) -> dict:
        with self._lock:
            mode = self.mode
            p = self.bench.folder if mode == "bench" else self.part.path if mode == "part" \
                else (self.test or self.scenario or self.robot).path if mode == "robot" else None
            unset = self._needs_folder()
            back = self.back if mode == "part" else None
            test = str(self.test.path) if self.test is not None else None
        return {"mode": mode, "test": test, "path": str(p) if p is not None else None,
                "display_path": self._display(p) if p is not None else "", "recent": recent.recent(),
                "back": {"path": str(back), "display_path": self._display(back)} if back is not None else None,
                "examples": bench_session.examples(), "needs_folder": unset[0] if unset else None}

    def _needs_folder(self) -> tuple[str, str] | None:
        """The team folder the open robot file (or the scenario's `config`) names that this computer hasn't set."""
        for d in (self.robot, self.scenario):
            if d is not None and d.doc is not None:
                unset = unset_folder(d.doc.unwrap())
                if unset is not None:
                    return unset
        return None

    def get_folders(self) -> dict:
        known = teamfolders.folders()
        return {**{k: str(v) if v is not None else None for k, v in known.items()},
                "from_env": {k: bool(os.environ.get(f"SCRIMMAGE_{k.upper()}")) for k in teamfolders.FOLDERS}}

    def set_folder(self, body: dict) -> dict:
        """Remember where a team folder is on this computer; what is open is read again with it."""
        name, path = body.get("name"), body.get("path")
        if name not in teamfolders.FOLDERS:
            raise ApiError(400, f"name must be one of {', '.join(teamfolders.FOLDERS)}")
        p = None
        if path is not None:
            p = self._user_path(path, "a folder path")
            if not p.is_dir():
                raise ApiError(400, f"{self._display(p)} is not a folder")
        try:
            teamfolders.set_folder(name, p)
        except OSError as e:
            raise ApiError(500, f"can't save the team folders: {e.strerror or e}") from None
        with self._lock:
            if self.mode == "robot":
                if self.robot is not None and self.robot.doc is not None:
                    self.robot.read_only_reason = None   # recomputed below: it may have been the unset folder
                if not (self.scenario is not None and self._point_robot()):
                    if self.robot is not None:
                        self.robot.version += 1
                    else:
                        self._robot_ver += 1
                self._revalidate()
                self._revalidate_scenario(bump=True)
        return {"ok": True}

    def browse(self, folder) -> dict:
        d = self._user_path(folder, "a folder path") if folder else self.cwd
        if not d.is_dir():
            raise ApiError(400, f"{self._display(d)} is not a folder")
        try:
            items = sorted(d.iterdir(), key=lambda p: p.name.lower())
        except OSError as e:
            raise ApiError(400, f"can't read {self._display(d)}: {e.strerror or e}") from None
        folders, files = [], []
        for p in items:
            if p.name.startswith("."):
                continue
            if p.is_dir():
                folders.append({"name": p.name, "path": str(p), "kind": "folder"})
            elif p.is_file() and p.suffix.lower() == ".toml":
                files.append({"name": p.name, "path": str(p), "kind": classify(p)})
            elif p.is_file() and p.suffix.lower() in part_session.CAD_SUFFIXES:
                files.append({"name": p.name, "path": str(p), "kind": "cad"})
        return {"dir": str(d), "parent": str(d.parent) if d.parent != d else None, "entries": folders + files}

    def open_path(self, path, kind: str | None = None, parts_dir: Path | None = None) -> dict:
        """Open a robot config, scenario, bench file (its folder as the session, that file on stage), folder, import
        file, CAD file or part folder (see _load for kind and parts_dir)."""
        p = self._user_path(path, "a file or folder path")
        if not p.exists():
            raise ApiError(400, f"{self._display(p)} doesn't exist")
        if p.is_file() and p.suffix.lower() not in (".toml", *part_session.CAD_SUFFIXES):
            raise ApiError(400, f"{self._display(p)} is not a robot config, scenario, bench file, CAD file or folder")
        with self._lock:
            self._check_can_open()
            try:
                with self.bench_jobs.gate():   # no job can queue on the old session while it is replaced
                    self._load(p, kind=kind, parts_dir=parts_dir)
            except OSError as e:
                raise ApiError(400, f"can't read {self._display(p)}: {e.strerror or e}") from None
            except (CadError, TestFileError) as e:
                raise ApiError(400, str(e)) from None
        self.bench_jobs.stop()    # idle (refused above while a job is active); outside the lock: it joins
        self.bench_jobs.clear()
        remember_opened(p)
        self.remember_part()
        return {"ok": True}

    def new_import(self, body: dict) -> dict:
        """New part from CAD: open a Browse-picked source in place (its import file in parts/ next to it, or next
        to the robot config `robot`)."""
        p = self._user_path(body.get("path"), "the CAD file to import")
        if not (p.is_file() and p.suffix.lower() in part_session.CAD_SUFFIXES) and not (
                p.is_dir() and any(f.suffix.lower() in part_session.CAD_SUFFIXES for f in p.iterdir())):
            raise ApiError(400, f"{self._display(p)} is not a STEP, STL or OBJ file (or a folder of STL/OBJ files)")
        parts_dir = None
        if body.get("robot") is not None:
            robot = self._user_path(body.get("robot"), "the robot config the part is for")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, f"{self._display(robot)} is not a robot config")
            parts_dir = robot.parent / "parts"
        return self.open_path(str(p), kind="import", parts_dir=parts_dir)

    def upload(self, stream, length: int, filename: str | None, files: list | None = None,
               again: bool = False) -> tuple[int, dict]:
        """A dropped CAD file: into the open import's parts/<name>.source/ (re-imported from there), or, with no
        part open, a new import in parts/ (next to the robot config in robot mode, else the working directory).
        `files` ([(name, size)], more than one) is several STL or OBJ files sent one after another: they go in
        .source/meshes/ and are read together, as a folder of them. `again` is Import again on a copied source:
        the settings are kept as for an ordinary re-import."""
        if files is not None and len(files) == 1:
            filename, files = files[0][0], None

        def save(folder: Path) -> Path:
            if files is None:
                return part_session.save_upload(stream, length, filename, folder)
            return part_session.save_uploads(stream, length, files, folder)
        with self._lock:
            part, base = self.part, (self._base_dir() if self.mode == "robot" else self.cwd)
        if part is not None:
            return part.replace_source(save(part.source_folder()), again)
        with self._lock:
            self._check_can_open()
        if files is None:
            name = sanitize(Path(part_session.upload_name(filename)).stem)
        else:
            names = [part_session.upload_name(n) for n, _ in files]
            part_session.upload_set(names)
            name = part_session.upload_stem(names)
        imp = part_session.free_import(base / "parts", name)
        dest = save(imp.with_name(imp.name[:-len(part_session.SUFFIX)] + ".source"))
        with self._lock:
            self._check_can_open()
            with self.bench_jobs.gate():
                self._load(imp, kind="import", source=dest)
        self.bench_jobs.stop()
        self.bench_jobs.clear()
        self.remember_part()
        return 200, self.part.state()

    def new(self, body: dict) -> dict:
        """Write a new robot config (from a template), scenario (for a robot config) or session folder, then open
        it. A path that exists is refused; a robot or scenario path without .toml gets it."""
        kind = body.get("kind")
        if kind == "import":
            return self.new_import(body)
        if kind not in ("robot", "scenario", "session"):
            raise ApiError(400, "kind must be robot, scenario, session or import")
        p = self._user_path(body.get("path"), "a file or folder path")
        if kind != "session" and p.suffix.lower() != ".toml":
            p = p.with_name(p.name + ".toml")
        if p.exists():
            raise ApiError(400, f"{self._display(p)} already exists; choose another name")
        self._check_can_open()
        text = None
        if kind == "robot":
            if body.get("template") not in ROBOT_TEMPLATES:
                raise ApiError(400, f"template must be one of {', '.join(ROBOT_TEMPLATES)}")
            text = templates.new_config(body["template"])
        elif kind == "scenario":
            robot = self._user_path(body.get("robot"), "the robot config the scenario runs")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, f"{self._display(robot)} is not a robot config")
            text = templates.new_scenario(self._portable(robot, p.parent))
        try:
            if text is None:
                p.mkdir(parents=True)
            else:
                with open(p, "x", encoding="utf-8") as f:
                    f.write(text)
        except FileExistsError:
            raise ApiError(400, f"{self._display(p)} already exists; choose another name") from None
        except OSError as e:
            raise ApiError(400, f"can't write {self._display(p)}: {e.strerror or e}") from None
        return self.open_path(str(p))

    # -- documents --------------------------------------------------------------------------
    @property
    def robot_version(self) -> int:
        return self.robot.version if self.robot is not None else self._robot_ver

    @staticmethod
    def _config_named(d, scenario_path: Path) -> Path | None:
        """The robot config a parsed scenario `d` names (resolved against its folder), or None (d is None for a
        TOML error, or it has no string `config`)."""
        c = _plain(d.get("config")) if d is not None else None
        if not isinstance(c, str):
            return None
        try:
            c = teamfolders.expand(c)
        except teamfolders.TeamFolderError:
            return None   # _point_robot says which folder to set
        return (scenario_path.parent / c).resolve()

    def _scenario_config(self, sc: ConfigDoc | None) -> Path | None:
        """The robot config the scenario document `sc` names, or None (no scenario, a TOML error, no `config`)."""
        return self._config_named(sc.doc, sc.path) if sc is not None else None

    def _check_live_robot(self, target: Path | None) -> None:
        """While a drive session is live (starting, running or paused), the robot document must stay its config:
        409 when `target` (the robot config a scenario change would point at) is another one, or isn't known
        (None) or readable. Called with self._lock held."""
        st = self.live.status()
        if st["state"] not in ("starting", "running", "paused"):
            return
        live = Path(st["config"]).resolve()
        if target is not None and target.is_file() and os.access(target, os.R_OK):
            if target.resolve() == live:
                return
            try:
                if os.path.samefile(target, live):
                    return
            except OSError:
                pass
        raise ApiError(409, "a drive session is live")

    def _detach(self, reason: str, target: Path | None, initial: bool) -> bool:
        """No robot document, for `reason`; True when that changed."""
        if self.robot is None and self.robot_missing == reason:
            return False
        if not initial:
            self._robot_ver = self.robot_version + 1
        self.robot, self.robot_missing, self._robot_target = None, reason, target
        return not initial

    def _point_robot(self, initial: bool = False) -> bool:
        """Make the robot document the scenario's config; True when it was replaced, detached or reattached."""
        sc = self.scenario
        target = self._scenario_config(sc)
        if target is None:
            if sc.doc is None:
                reason = (f"the scenario {sc.path.name} has a TOML error, so its robot config isn't known; "
                          "fix the scenario in a text editor")
            else:
                unset = unset_folder(_plain(sc.doc.get("config")))
                reason = unset[1] if unset else 'this scenario names no robot config (config = "<robot config .toml>")'
            return self._detach(reason, None, initial)
        return self._attach_robot(target, initial)

    def _point_test_robot(self, initial: bool = False) -> bool:
        """Make the robot document the test's robot; True when it was replaced, detached or reattached."""
        return self._attach_robot(self.test.robot_path, initial)

    def _attach_robot(self, target: Path, initial: bool) -> bool:
        """Make the robot config `target` the robot document (a scenario's or a test's); True when it was replaced,
        detached or reattached."""
        if self.robot is not None and self.robot.path == target:
            if target.exists():
                return False
            return self._detach(f"the robot config {self._display(target)} was deleted", target, initial)
        doc, reason = None, None
        try:
            doc = ConfigDoc(target)
        except FileNotFoundError:
            reason = f"the robot config {self._display(target)} doesn't exist"
        except OSError as e:
            reason = f"can't read the robot config {self._display(target)}: {e.strerror or e}"
        if doc is None:
            if self.robot is None and self._robot_target == target and self.robot_missing is not None:
                return False   # still missing: keep saying why (e.g. "was deleted")
            return self._detach(reason, target, initial)
        doc.version = 0 if initial else self.robot_version + 1
        self.robot, self.robot_missing, self._robot_target = doc, None, target
        return True

    def _revalidate(self) -> None:
        """Validate the robot as it is now; a hand-written or invalid config becomes read-only."""
        self._view = None
        self._scenario_view = None
        self._signals = None
        self._parts_seen = self._parts_look()
        if self.robot is None:
            self.result = validate.Result(False, [validate.FieldError("status", self.robot_missing)], [], None)
        else:
            self.result = validate.check(self.robot.text, self.robot.path)
            if self.robot.read_only_reason is None:
                unset = unset_folder(self.robot.doc.unwrap()) if self.robot.doc is not None else None
                if not self.result.ok and unset is not None:
                    self.robot.read_only_reason = unset[1]
                elif not self.result.ok:
                    self.robot.read_only_reason = ("this config has errors; fix them in a text editor: "
                                                   + "; ".join(e.message for e in self.result.errors))
                    d = self.robot.doc
                    if self.scenario is None and d is not None and "part" not in d and "model" not in d:
                        self.robot.read_only_reason += ("; to start a new one, use scrimmage edit FILE --new "
                                                        "swerve|differential or --new-scenario --robot CONFIG")
                elif self.result.world.parts is None:
                    self.robot.read_only_reason = HAND_WRITTEN
        if self.test is not None:
            self.test.refresh(self._robot_world(), self._robot_problem())
        self._changed.notify_all()

    def _robot_problem(self) -> str | None:
        """Why the robot document has no World, in a sentence (None when it has one)."""
        if self._robot_world() is not None:
            return None
        if self.robot is None:
            return self._rel(self.robot_missing)
        return self._rel(f"{self.robot.path.name} has errors: " + "; ".join(e.message for e in self.result.errors))

    def _scenario_signature(self):
        r = self.scenario_result
        return None if r is None else (r.ok, self.scenario_errors, _fe(r.warnings), self.scenario_robot_problem,
                                       self.scenario_read_only_reason)

    def _rel(self, text: str | None) -> str | None:
        """Paths under the working directory shown relative to it."""
        return text.replace(str(self.cwd) + os.sep, "") if text else text

    def _set_scenario_result(self, res: scenario_validate.Result, bump: bool = False) -> None:
        before = self._scenario_signature()
        self.scenario_result = res
        problem = res.robot_problem
        if self.robot is None and self.robot_missing is not None and (problem or self.scenario.doc is None):
            problem = self.robot_missing   # our own words: "was deleted", "has a TOML error", ...
        problem = self._rel(problem)
        self.scenario_robot_problem = problem
        old = f"fix the robot first: {res.robot_problem}" if res.robot_problem else None
        self.scenario_errors = [{"field": e.field,
                                 "message": f"fix the robot first: {problem}" if e.message == old else e.message}
                                for e in res.errors]
        self.scenario_read_only_reason = self.scenario.read_only_reason or (
            f"fix the robot first: {problem}" if res.robot_problem else None)
        if bump and self._scenario_signature() != before:
            self.scenario.version += 1   # the page's next state shows the new errors / read-only state
        self._changed.notify_all()

    def _refusal(self, res: scenario_validate.Result) -> list[dict] | None:
        """Why a candidate scenario can't be written, or None. A valid file only takes a valid candidate; an invalid
        one (its robot fine) takes a candidate with no more errors, so fixing one error may reveal the next."""
        cur = self.scenario_result
        if res.ok:
            return None
        if res.robot_problem is None and not cur.ok and cur.robot_problem is None \
                and len(res.errors) <= len(cur.errors):
            return None
        return _fe(res.errors)

    def _revalidate_scenario(self, bump: bool = False) -> None:
        if self.scenario is not None:
            self._set_scenario_result(scenario_validate.check(self.scenario.text, self.scenario.path), bump)

    def _sync(self) -> bool:
        """Pick up outside edits of either file; True when something changed."""
        s_changed = self.scenario is not None and self.scenario.reload_if_changed()
        r_changed = self.scenario is not None and self._point_robot()
        if self.test is not None:
            self.test.reload_if_changed()
            r_changed = self._point_test_robot() or r_changed
        if self.robot is not None and self.robot.reload_if_changed():
            r_changed = True
        if self.robot is not None and self._parts_look() != self._parts_seen:
            # a part folder changed (Import again rewrites the folder, not the robot config): build the robot again,
            # past MuJoCo's cache, which keeps a mesh by its file name and time to the second
            mujoco.mj_clearCache(mujoco.mj_getCache())
            if not r_changed:
                self.robot.version += 1
            r_changed = True
        if r_changed:
            self._revalidate()
        if s_changed:
            self._revalidate_scenario()
        elif r_changed:
            self._revalidate_scenario(bump=True)
        return bool(s_changed or r_changed)

    def _check_fresh(self, body: dict, current) -> None:
        """`current` is a callable giving the document's version after a sync."""
        v = body.get("version")
        if not isinstance(v, int) or isinstance(v, bool):
            raise ApiError(400, "version (the integer from the state) is required")
        before = current()
        if self._sync() and current() != before:
            raise ApiError(409, FileChanged().message)
        if v != current():
            raise ApiError(409, "the file changed (another tab?); the page has reloaded it")

    def _display(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.cwd))
        except ValueError:
            return str(path)

    def _wait(self, since: int | None, current, make) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._lock:
            while True:
                self._sync()
                if since is None or current() != since or time.monotonic() >= end:
                    return make()
                self._changed.wait(1.0)

    def _robot_world(self):
        """The robot's world when its config loads (hand-written robots included), else None."""
        return self.result.world if self.result is not None and self.result.ok else None

    # -- robot ------------------------------------------------------------------------------
    def state(self) -> dict:
        d = self.robot
        parts = []
        other = ""
        if d is not None and d.doc is not None:
            for p in edits.parts(d.doc):
                reserved = {"name", "stock", "path", "attach", "pos", "rot", "ids"}
                parts.append({"name": _plain(p.get("name")), "stock": _plain(p.get("stock")),
                              "path": _plain(p.get("path")), "attach": _plain(p.get("attach")),
                              "pos": _plain(p.get("pos", [0.0, 0.0, 0.0])), "rot": _plain(p.get("rot", [0.0, 0.0, 0.0])),
                              "params": {k: _plain(v) for k, v in p.items() if k not in reserved},
                              "ids": _plain(p.get("ids", {})),
                              "cad_pos": self._cad_pos(d.doc, _plain(p.get("path")), _plain(p.get("attach"))),
                              "cad": self._cad_info(d.doc, _plain(p.get("path")))})
            rest = tomlkit.parse(d.text)
            rest.pop("part", None)
            other = tomlkit.dumps(rest)
        elif d is not None:
            other = d.text
        reason = d.read_only_reason if d is not None else self.robot_missing
        out = {"version": self.robot_version, "path": str(d.path) if d is not None else None,
               "display_path": self._display(d.path) if d is not None else "",
               "read_only": reason is not None, "read_only_reason": reason, "parts": parts,
               "errors": _fe(self.result.errors), "warnings": _fe(self.result.warnings),
               "other_sections": other, "can_undo": d is not None and d.can_undo,
               "can_redo": d is not None and d.can_redo,
               "scenario_open": self.scenario is not None,
               "mass_kg": None, "bodies": None, "joints": None, "part_bodies": None, "com": None,
               "range": self._range_file(), "start": self._range_start()}
        w = self.result.world if self.result.ok else None
        if w is not None and w.parts is not None:
            m = w.model
            root = int(m.body_rootid[w.robot_body])
            out["mass_kg"] = float(m.body_subtreemass[w.robot_body])
            out["part_bodies"] = {i.name: [b.name for b in i.part.bodies] for i in w.parts.instances}
            out["bodies"] = sum(1 for b in range(1, m.nbody) if int(m.body_rootid[b]) == root)
            out["joints"] = sum(1 for j in range(m.njnt) if int(m.body_rootid[m.jnt_bodyid[j]]) == root
                                and int(m.jnt_type[j]) != int(mujoco.mjtJoint.mjJNT_FREE))
            out["com"] = self._robot_com(w)
        return out

    def _range_file(self) -> str | None:
        """The robot document's [range] file as written (a built-in range's name or a path); None: the plain field."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        return sec.get("file") if sec is not None and isinstance(sec.get("file"), str) else None

    def _range_start(self) -> str | None:
        """The robot document's [range] start (a named start of its field) as written; None when it names none."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        return sec.get("start") if sec is not None and isinstance(sec.get("start"), str) else None

    def _robot_com(self, w) -> dict:
        """The robot's center of gravity at its start pose: subtree_com of the robot body in that body's frame (m;
        for a chassis root, its origin is on the floor at the frame center). Kept until the world changes."""
        if self._com is None or self._com[0] is not w:
            m = w.model
            d = mujoco.MjData(m)
            mujoco.mj_kinematics(m, d)
            mujoco.mj_comPos(m, d)
            b = w.robot_body
            x, y, z = d.xmat[b].reshape(3, 3).T @ (d.subtree_com[b] - d.xpos[b])
            self._com = (w, {"height": float(z), "forward": float(x), "left": float(y)})
        return self._com[1]

    def _part_folder(self, path) -> Path | None:
        """The folder a [[part]] `path` names (relative to the robot config, or {cad}/...), or None."""
        if not isinstance(path, str) or not path.strip():
            return None
        try:
            p = Path(teamfolders.expand(path)).expanduser()
        except teamfolders.TeamFolderError:
            return None
        return p if p.is_absolute() else self._base_dir() / p

    def _part_files(self, path) -> list[Path]:
        """The files a [[part]] `path` reads: its part.toml, the plugin overlay beside it and the meshes it names (not
        the folder around them, which may be the robot's own, with its tests and their runs)."""
        folder = self._part_folder(path)
        if folder is None:
            return []
        toml = folder if folder.suffix == ".toml" else folder / "part.toml"
        files = [toml, toml.parent / "scrimmage-edits.toml"]
        try:
            st = toml.stat()
        except OSError:
            return files
        key = (str(toml), st.st_mtime_ns, st.st_size, st.st_ino)
        meshes = self._part_meshes.get(key)
        if meshes is None:
            try:
                raw = tomllib.loads(toml.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
                raw = {}
            bodies = raw.get("body") if isinstance(raw.get("body"), list) else []
            meshes = sorted({g["mesh"] for b in bodies if isinstance(b, dict)
                             for g in (b.get("geom") if isinstance(b.get("geom"), list) else [])
                             if isinstance(g, dict) and isinstance(g.get("mesh"), str)})
            if len(self._part_meshes) > 64:
                self._part_meshes.clear()
            self._part_meshes[key] = meshes
        return files + [toml.parent / m for m in meshes]

    def _parts_look(self) -> tuple | None:
        """Each file the robot document's parts read, with its time, size and file id, to see Import again."""
        d = self.robot
        if d is None or d.doc is None:
            return None
        out = []
        for p in edits.parts(d.doc):
            for f in self._part_files(p.get("path")):
                try:
                    st = f.stat()
                    out.append((str(f), st.st_mtime_ns, st.st_size, st.st_ino))
                except OSError:
                    out.append((str(f), None))      # not there (an overlay a part doesn't have), or being replaced
        return tuple(out)

    def _cad_pos(self, doc, path, attach) -> list[float] | None:
        """Where the part folder `path` attached to `attach` sits where the CAD has it (edits.cad_offset), or None:
        not from a CAD import, a target that isn't a body (a mount: known while the robot loads), or one with no CAD
        placement."""
        folder = self._part_folder(path)
        marker = library.cad_marker(folder) if folder is not None else None
        if marker is None or not isinstance(attach, str) or "." not in attach:
            return None
        inst, body = attach.split(".", 1)
        target = next((p for p in edits.parts(doc) if p.get("name") == inst), None)
        if target is None:
            return None
        w = self._robot_world()
        if w is not None and w.parts is not None and any(
                i.name == inst and any(mt.name == body for mt in i.part.mounts) for i in w.parts.instances):
            return None     # a mount wins over a body of the same name
        tfolder = self._part_folder(_plain(target.get("path")))
        return edits.cad_offset(marker, body, library.cad_marker(tfolder) if tfolder is not None else None,
                                chassis_root=_plain(target.get("stock")) == "chassis" and body == _chassis_root_body())

    def _cad_info(self, doc, path) -> dict | None:
        """{import_id, part, hangs_on} for a part folder from a CAD import, else None. hangs_on: what the CAD hangs it
        on, as an attach target: "<instance>.<body>" when a part from the same import with that name is in the robot,
        else the marker's "<part>.<body>"; None when the robot carries it."""
        folder = self._part_folder(path)
        marker = library.cad_marker(folder) if folder is not None else None
        if marker is None:
            return None
        hangs_on = marker["attach"]
        if hangs_on is not None:
            part, body = hangs_on.split(".")
            for p in edits.parts(doc):
                f = self._part_folder(_plain(p.get("path")))
                m = library.cad_marker(f) if f is not None else None
                if m is not None and m["import_id"] == marker["import_id"] and m["part"] == part:
                    hangs_on = f"{_plain(p.get('name'))}.{body}"
                    break
        return {"import_id": marker["import_id"], "part": marker["part"], "hangs_on": hangs_on}

    def wait_state(self, since: int | None) -> dict:
        return self._wait(since, lambda: self.robot_version, self.state)

    def view(self) -> dict:
        with self._lock:
            w = self._robot_world()
            if self.robot is None or self.robot.read_only_reason is not None or w is None or w.parts is None:
                return {}
            if self._view is None:
                self._view = model_view(w)
            return self._view

    def _base_dir(self) -> Path:
        return (self.robot or self.scenario or self.test).path.parent

    def library(self) -> dict:
        with self._lock:
            base = self._base_dir()
            dirs = [base, base / "parts", *self.extra_folders]   # parts/ is where the FreeCAD exporter puts them
            return {"stock": library.stock(), "folders": library.folders(dirs, base)}

    def add_folder(self, path) -> dict:
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "path must be a folder path")
        p = Path(path).expanduser()
        with self._lock:
            if not p.is_absolute():
                p = self._base_dir() / p
            p = p.resolve()
            if not p.is_dir():
                raise ApiError(400, f"{p} is not a folder")
            if p not in self.extra_folders:
                self.extra_folders.append(p)
        return {"ok": True}

    def _require_robot_writable(self) -> None:
        if self.robot is None:
            raise ApiError(409, self.robot_missing)
        if self.robot.read_only_reason is not None:
            raise ApiError(409, self.robot.read_only_reason)

    def _stock_of(self, name) -> str | None:
        for p in edits.parts(self.robot.doc):
            if p.get("name") == name:
                return _plain(p.get("stock"))
        return None

    def _apply(self, op: str, b: dict):
        name = b.get("name")
        if op == "add":
            path = b.get("path")
            pos = b.get("pos")
            if pos is None:     # a part from a CAD import goes where the CAD has it
                pos = self._cad_pos(self.robot.doc, path, b.get("attach"))
            if isinstance(path, str) and path.strip() and not _FOLDER_PLACEHOLDER.search(path):
                portable = self._portable((self._base_dir() / Path(path).expanduser()).resolve(), self._base_dir())
                path = portable if portable.startswith("{") else path
            return lambda doc: edits.add_part(doc, name=name, stock=b.get("stock"), path=path,
                                              attach=b.get("attach"), pos=pos, rot=b.get("rot"),
                                              params=b.get("params"), ids=b.get("ids"),
                                              defaults=_stock_defaults(b.get("stock")))
        if op == "remove":
            return lambda doc: edits.remove_part(doc, name)
        if op == "rename":
            return lambda doc: edits.rename_part(doc, name, b.get("new_name"))
        if op == "attach":
            return lambda doc: edits.set_attach(doc, name, b.get("attach"), b.get("pos"), b.get("rot"))
        if op == "params":
            defaults = _stock_defaults(self._stock_of(name))
            return lambda doc: edits.set_params(doc, name, b.get("params") or {}, defaults)
        if op == "ids":
            return lambda doc: edits.set_ids(doc, name, b.get("ids") or {})
        if op == "start":
            keys = ("range", "pose", "named", "keep_pose", "floor_balls")
            return lambda doc: edits.set_start(doc, {k: b[k] for k in keys if k in b})
        if op == "battery":
            return lambda doc: edits.set_battery(doc, {k: b[k] for k in ("nominal_voltage", "resistance") if k in b})
        if op == "match_devices":
            return lambda doc: edits.match_devices(doc, b.get("matches"))
        if op == "motor":
            world = self.result.world if self.result is not None and self.result.ok else None
            motor_slots = {m.name for m in world.motors} if world is not None else None
            return lambda doc: edits.set_motor(doc, b.get("slot"), motor_slots=motor_slots,
                                               **{k: b[k] for k in ("neutral", "invert") if k in b})
        raise ApiError(400, f"unknown op {op!r} (add, remove, rename, attach, params, ids, start, battery, "
                            "match_devices, motor)")

    def edit(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._check_fresh(body, lambda: self.robot_version)
            self._require_robot_writable()
            status, out = self._commit(self._apply(body.get("op"), body))
            if body.get("op") == "match_devices" and out.get("ok"):
                out["restarting"], problem = self._restart_live()
                if problem is not None:
                    out["restart_problem"] = problem
            return status, out

    def _restart_live(self) -> tuple[bool, str | None]:
        """Ask the live session on the robot document to restart after its IDs changed: (restarting, why not, when
        it couldn't: an unsaved recording). The restart runs on its own thread; /api/robot/devices' restart says
        how it went. Called with self._lock held."""
        st = self.live.status()
        if st["state"] not in ("starting", "running", "paused") or st["config"] is None \
                or Path(st["config"]) != self.robot.path.resolve():
            return False, None
        try:
            self.live.restart()
        except UnsavedRecording as e:
            return False, f"the running robot wasn't restarted with the new IDs: {e}"
        except LiveError:
            return False, None    # it ended meanwhile
        return True, None

    def _commit(self, fn) -> tuple[int, dict]:
        """Apply the edit `fn` to the robot document and write it when the result validates (one undo step).
        Called with self._lock held, the document writable."""
        try:
            candidate = self.robot.apply(fn)
        except EditError as e:
            return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
        except (TypeError, ValueError, AttributeError) as e:   # malformed JSON values from a client
            return 200, {"ok": False, "errors": [{"field": "status", "message": f"bad edit: {e}"}]}
        res = validate.check(candidate, self.robot.path)
        if not res.ok:
            return 200, {"ok": False, "errors": _fe(res.errors)}
        if res.world.parts is None:
            return 200, {"ok": False, "errors": [{"field": "status", "message": HAND_WRITTEN}]}
        try:
            self.robot.commit(candidate)
        except FileChanged as e:
            self._sync()
            raise ApiError(409, e.message) from None
        except EditError as e:
            return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
        except OSError as e:
            return 200, {"ok": False, "errors": [{"field": "status", "message": f"can't write the file: {e}"}]}
        self._view = self._scenario_view = self._signals = None
        self.result = res
        self._parts_seen = self._parts_look()
        if self.test is not None:      # its palette and problems: an edit can change the field or the parts
            self.test.refresh(self._robot_world(), self._robot_problem())
        self._revalidate_scenario(bump=True)
        self._changed.notify_all()
        return 200, {"ok": True, "version": self.robot.version}

    def history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._check_fresh(body, lambda: self.robot_version)
            self._require_robot_writable()
            target = self.robot.peek_undo() if which == "undo" else self.robot.peek_redo()
            if target is None:
                return 200, {"ok": False, "errors": [{"field": "status", "message": f"nothing to {which}"}]}
            res = validate.check(target, self.robot.path)   # the world may have changed (a deleted part folder)
            if not res.ok or res.world.parts is None:
                errs = _fe(res.errors) if not res.ok else [{"field": "status", "message": HAND_WRITTEN}]
                return 200, {"ok": False, "errors": [{"field": e["field"], "message": f"can't {which}: {e['message']}"}
                                                      for e in errs]}
            try:
                self.robot.undo() if which == "undo" else self.robot.redo()
            except FileChanged as e:
                self._sync()
                raise ApiError(409, e.message) from None
            except OSError as e:
                raise ApiError(500, f"can't write the file: {e}") from None
            self._revalidate()
            self._revalidate_scenario(bump=True)
            return 200, {"ok": True, "version": self.robot.version}


    # -- robot code, where it starts, battery (the Robot tab's robot code panel) --------------------------------
    def _portable(self, target: Path, base: Path) -> str:
        """How a robot file (or scenario) in `base` names `target`: `{cad}/...` or `{code}/...` when a team folder
        holds `target` but not `base` (so the file works on every computer), else relative to `base` ("/"-separated;
        absolute on another drive)."""
        target, base = Path(target).resolve(), Path(base).resolve()
        named = teamfolders.relativize(target)
        if named.startswith("{"):
            root = teamfolders.folders()[named[1:named.index("}")]]
            if not base.is_relative_to(root.resolve()):
                return named
        try:
            return Path(os.path.relpath(target, base)).as_posix()
        except ValueError:   # another drive (Windows)
            return target.as_posix()

    def _program_folder(self) -> tuple[str | None, Path | None, str | None]:
        """(the [robot_program] cwd as written, the folder it names, why it can't be used) for the robot document;
        all None without a [robot_program]. Called with self._lock held."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        rp = raw.get("robot_program")
        if not isinstance(rp, dict):
            return None, None, None
        cwd = rp.get("cwd", ".")
        if not isinstance(cwd, str):
            return None, None, f"[robot_program] cwd must be a folder path, not {cwd!r}"
        try:
            folder = (self.robot.path.parent / teamfolders.expand(cwd)).resolve()
        except teamfolders.TeamFolderError as e:
            return cwd, None, str(e)
        if not folder.is_dir():
            return cwd, folder, (f"the robot code folder {self._display(folder)} isn't there (was it moved?): "
                                 "pick it again")
        return cwd, folder, None

    def _named_starts(self, ref: str, base: Path) -> dict | None:
        """The named starts of range `ref` (a built-in name or a path relative to `base`), None when it doesn't
        load. Cached by the file's modification time."""
        try:
            f = BUILTIN_RANGES.get(ref) or base / teamfolders.expand(ref)
            key = (str(f), f.stat().st_mtime_ns)
            if key not in self._ranges:
                rng = load_range(ref, base)
                self._ranges[key] = {k: [float(a) for a in v] for k, v in rng.starts.items()}
            return self._ranges[key]
        except (RangeError, OSError, ValueError):
            return None

    def _start(self) -> dict:
        """Where the robot document's robot starts and on which field. Called with self._lock held."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        robot = raw.get("robot") if isinstance(raw.get("robot"), dict) else {}
        sec = raw.get("range") if isinstance(raw.get("range"), dict) else None
        rng = sec.get("file") if sec is not None and isinstance(sec.get("file"), str) else None
        named = sec.get("start") if sec is not None and isinstance(sec.get("start"), str) else None
        starts = (self._named_starts(rng, self.robot.path.parent) or {}) if rng is not None else {}
        written = robot.get("start_pose")
        pose = None
        if named is not None:
            pose = starts.get(named)
        elif isinstance(written, list) and len(written) == 3 and all(_is_num(a) for a in written):
            pose = [float(a) for a in written]
        picked = written is not None or named is not None
        if pose is not None:
            sentence = start_sentence(pose)
        else:
            sentence = f"Starts at {named}" if named is not None else NOT_PICKED if not picked else "Starts as written"
        floor = sec.get("floor_balls", True) if sec is not None else True
        return {"picked": picked, "pose": pose, "range": rng, "named": named, "named_starts": list(starts),
                "floor_balls": floor if isinstance(floor, bool) else True, "fields": list(edits.FIELDS),
                "range_read_only": rng is not None and rng not in edits.FIELDS, "sentence": sentence}

    def _battery(self) -> dict:
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        b = raw.get("battery") if isinstance(raw.get("battery"), dict) else {}
        return {"nominal_voltage": b.get("nominal_voltage"), "resistance": b.get("resistance"),
                "defaults": dict(BATTERY_DEFAULTS)}

    def _start_problem(self, for_run: bool = False) -> str | None:
        """UNPICKED while the robot document (a [[part]] robot) has neither [robot] start_pose nor [range] start
        (for a run, nor does the scenario's [range] give a start), else None. A hand-written model starts where its
        MJCF puts it. Only the page asks: the CLI and the loader keep their default. Called with self._lock held."""
        if self.robot is None or self.robot.doc is None:
            return None
        raw = self.robot.doc.unwrap()
        if "part" not in raw or self._start()["picked"]:
            return None
        if for_run and self.scenario is not None and self.scenario.doc is not None:
            sec = self.scenario.doc.unwrap().get("range")
            if isinstance(sec, dict) and sec.get("start") is not None:
                return None
        return UNPICKED

    def code_state(self) -> dict:
        with self._lock:
            self._sync()
            cwd, folder, problem = self._program_folder()
            out = {"folder": str(folder) if folder is not None else None,
                   "display_folder": self._display(folder) if folder is not None else None,
                   "cwd": cwd, "problem": problem, "info": None, "checks": [], "start": self._start(),
                   "battery": self._battery()}
        if folder is not None and problem is None:   # outside the lock: the checks run programs
            info = robotcode.detect(folder)
            if info.kind is None:
                out["problem"] = info.summary
            else:
                out["info"] = _info_json(info)
                failed = self._failed_build(folder)
                out["checks"] = [failed if c.id == "gradle_built" and failed is not None else dataclasses.asdict(c)
                                 for c in robotcode.checklist(info)]
        return out

    def _failed_build(self, folder: Path) -> dict | None:
        """The failed gradle_built check of this folder's last Build now, while nothing has built there since (a
        build in VS Code counts), else None."""
        with self._fix_cond:
            got = self._build_failed.get(str(folder))
        if got is None:
            return None
        built = robotcode.built_at(folder)
        return got[1] if built is None or built < got[0] else None

    def _not_built(self) -> str | None:
        """Why the robot document's Java code can't start yet (the page's starts only): it has never built on this
        computer, its last Build now failed, or a build is running. None for RobotPy, a hand-written program or a
        folder that can't be used (the start says why). Called with self._lock held."""
        _, folder, problem = self._program_folder()
        if folder is None or problem is not None or not robotcode.is_java(folder):
            return None
        with self._fix_cond:
            building = self._fix["running"] and self._fix["fix"] == "gradle_build"
        if building:
            return NOT_BUILT_RUNNING
        if self._failed_build(folder) is not None:
            return NOT_BUILT_FAILED
        if robotcode.built_at(folder) is None:
            return NOT_BUILT
        return None

    def code_set(self, body: dict) -> tuple[int, dict]:
        """Point the robot at its code: detect the project in `folder` and write [robot_program] (one undo step)."""
        p = self._user_path(body.get("folder"), "a folder path")
        if not p.is_dir():
            raise ApiError(400, f"{self._display(p)} is not a folder")
        info = robotcode.detect(p)
        if info.kind is None:
            raise ApiError(400, info.summary)
        with self._lock:
            if "version" in body:
                self._check_fresh(body, lambda: self.robot_version)
            else:
                self._sync()
            self._require_robot_writable()
            table = {**robotcode.program_table(info), "cwd": self._portable(info.folder, self.robot.path.parent)}
            status, out = self._commit(lambda doc: edits.set_program(doc, table))
        if out.get("ok"):
            out["info"] = _info_json(info)
        return status, out

    def fix_state(self) -> dict:
        with self._fix_cond:
            return {**self._fix, "lines": list(self._fix["lines"])}

    def wait_fix(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._fix_cond:
            while since is not None and self._fix["version"] == since and time.monotonic() < end:
                self._fix_cond.wait(1.0)
            return {**self._fix, "lines": list(self._fix["lines"])}

    def code_fix(self, body: dict) -> dict:
        """Run one readiness fix (robotcode.run_fix) on the fix worker: one at a time."""
        fix = body.get("fix")
        if fix not in FIXES:
            raise ApiError(400, f"fix must be one of {', '.join(FIXES)}")
        with self._lock:
            _, folder, problem = self._program_folder()
        if folder is None or problem is not None:
            raise ApiError(400, problem or "pick the robot code's folder first")
        info = robotcode.detect(folder)
        if info.kind is None:
            raise ApiError(400, info.summary)
        with self._fix_cond:
            if self._fix["running"]:
                raise ApiError(409, "a fix is already running; wait for it to finish")
            cancel = self._fix_cancel = threading.Event()
            f = self._fix
            f.update(id=f["id"] + 1, fix=fix, running=True, lines=[], result=None, version=f["version"] + 1)
            fix_id = f["id"]
            self._fix_cond.notify_all()

            def out(line: str) -> None:
                with self._fix_cond:
                    if self._fix["id"] == fix_id:
                        self._fix["lines"].append(line)
                        del self._fix["lines"][:-FIX_LINES]
                        self._fix["version"] += 1
                        self._fix_cond.notify_all()

            def work() -> None:
                try:
                    result = robotcode.run_fix(fix, info, out, cancel=cancel)
                except Exception as e:   # a bug-shaped failure still ends the fix with a message
                    logging.getLogger("scrimmage.assembler").exception("fix %s failed", fix)
                    result = robotcode.Check(fix, False, f"This didn't work: {type(e).__name__}: {e}", fix=fix)
                with self._fix_cond:
                    if fix == "gradle_build":
                        if result.ok:
                            self._build_failed.pop(str(info.folder), None)
                        else:
                            self._build_failed[str(info.folder)] = (time.time(), dataclasses.asdict(result))
                    self._fix.update(running=False, result=dataclasses.asdict(result),
                                     version=self._fix["version"] + 1)
                    self._fix_cond.notify_all()
            self._fix_thread = threading.Thread(target=work, daemon=True, name="robot-code-fix")
            self._fix_thread.start()
        return {"ok": True, "id": fix_id}

    # -- scenario ---------------------------------------------------------------------------
    def _need_scenario(self) -> ConfigDoc:
        if self.scenario is None:
            raise ApiError(404, "no scenario is open (scrimmage edit was given a robot config)")
        return self.scenario

    def scenario_state(self) -> dict:
        sc = self._need_scenario()
        res = self.scenario_result
        raw = sc.doc.unwrap() if sc.doc is not None else {}
        top = {k: raw.get(k) for k in ("config", "description", "speed", "require_lockstep")}
        steps = []
        t = 0.0
        for s in raw.get("step", []) if isinstance(raw.get("step"), list) else []:
            if not isinstance(s, dict):
                continue
            checks = []
            for c in s.get("check", []) if isinstance(s.get("check"), list) else []:
                if isinstance(c, dict) and len(c) == 1:
                    (kind, spec), = c.items()
                    checks.append({"kind": kind, "spec": spec})
                else:
                    checks.append({"kind": None, "spec": c})
            shots = s.get("screenshot", [])
            sheet = s.get("contact_sheet")
            steps.append({"name": s.get("name"), "mode": s.get("mode"), "duration": s.get("duration"), "start_t": t,
                          "inputs": s.get("inputs") if isinstance(s.get("inputs"), str) else None,
                          **{k: s.get(k) if isinstance(s.get(k), dict) else {} for k in ("axes", "buttons", "povs")},
                          "checks": checks,
                          "shots": [x for x in shots if isinstance(x, dict)] if isinstance(shots, list) else [],
                          "sheet": sheet if isinstance(sheet, dict) else None})
            d = s.get("duration")
            t += d if _is_num(d) and d > 0 else 0.0
        ro = self.scenario_read_only_reason
        return {"version": sc.version, "path": str(sc.path), "display_path": self._display(sc.path),
                "read_only": ro is not None, "read_only_reason": ro, "robot_problem": self.scenario_robot_problem,
                "top": top, "steps": steps, "errors": self.scenario_errors, "warnings": _fe(res.warnings),
                "can_undo": ro is None and sc.can_undo, "can_redo": ro is None and sc.can_redo}

    def scenario_track(self, step) -> dict:
        """The input track of step `step` for the Scenario tab's plot: {path, duration, t, series}; 404 when the
        step has no `inputs` or the file doesn't load."""
        with self._lock:
            sc = self._need_scenario()
            raw = sc.doc.unwrap() if sc.doc is not None else {}
            steps = raw.get("step") if isinstance(raw.get("step"), list) else []
            try:
                i = int(step)
            except (TypeError, ValueError):
                raise ApiError(400, "step must be an integer") from None
            s = steps[i] if 0 <= i < len(steps) and isinstance(steps[i], dict) else None
            if s is None or not isinstance(s.get("inputs"), str):
                raise ApiError(404, f"step {i} has no input track")
            try:
                track = load_track(sc.path.parent / s["inputs"])
            except TrackError as e:
                raise ApiError(404, str(e)) from None
            dur = s.get("duration")
        series: dict[str, list] = {}
        widths: dict[int, tuple[int, int, int]] = {}
        for smp in track.samples:
            for p, pi in smp.ports.items():
                widths[p] = (len(pi.axes), len(pi.buttons), len(pi.povs))
        for p, (na, nb, nv) in sorted(widths.items()):
            for k in range(na):
                series[f"p{p}.axis{k}"] = [smp.ports[p].axes[k] for smp in track.samples]
            for k in range(nb):
                series[f"p{p}.button{k + 1}"] = [int(smp.ports[p].buttons[k]) for smp in track.samples]
            for k in range(nv):
                series[f"p{p}.pov{k}"] = [smp.ports[p].povs[k] for smp in track.samples]
        return {"path": s["inputs"], "duration": dur, "t": [smp.t for smp in track.samples], "series": series}

    def wait_scenario_state(self, since: int | None) -> dict:
        with self._lock:
            self._need_scenario()
        # another file opened meanwhile: the version changes (or there is no scenario: 404)
        return self._wait(since, lambda: self.scenario.version if self.scenario is not None else None,
                          self.scenario_state)

    def _require_scenario_writable(self) -> None:
        if self.scenario_read_only_reason is not None:
            raise ApiError(409, self.scenario_read_only_reason)

    @staticmethod
    def _scenario_op(op: str, b: dict, scenario_path: Path, made: list):
        e = scenario_edits
        ops = {
            "top": lambda d: e.set_top(d, b.get("key"), b.get("value")),
            "add_step": lambda d: e.add_step(d, b.get("after"), b.get("name"), b.get("mode"), b.get("duration")),
            "duplicate_step": lambda d: e.duplicate_step(d, b.get("step")),
            "delete_step": lambda d: e.delete_step(d, b.get("step")),
            "move_step": lambda d: e.move_step(d, b.get("step"), b.get("to")),
            "set_step": lambda d: e.set_step(d, b.get("step"), b.get("fields") or {}),
            "inputs_file": lambda d: e.set_inputs_file(d, b.get("step"), b.get("path")),
            "trim_track": lambda d: made.append(e.trim_track(d, b.get("step"), b.get("start"), b.get("end"), scenario_path)),
            "inputs": lambda d: e.set_inputs(d, b.get("step"), b.get("kind"), b.get("port"), b.get("values")),
            "add_check": lambda d: e.add_check(d, b.get("step"), b.get("kind"), b.get("spec")),
            "set_check": lambda d: e.set_check(d, b.get("step"), b.get("check"), b.get("spec")),
            "delete_check": lambda d: e.delete_check(d, b.get("step"), b.get("check")),
            "add_shot": lambda d: e.add_shot(d, b.get("step"), b.get("spec") or {}),
            "set_shot": lambda d: e.set_shot(d, b.get("step"), b.get("shot"), b.get("spec")),
            "delete_shot": lambda d: e.delete_shot(d, b.get("step"), b.get("shot")),
            "sheet": lambda d: e.set_sheet(d, b.get("step"), b.get("spec")),
        }
        if op not in ops:
            raise ApiError(400, f"unknown op {op!r} ({', '.join(ops)})")
        return ops[op]

    def _check_config_change(self, sc: ConfigDoc, text: str) -> None:
        """409 when writing `text` would point the robot document away from a live session's config."""
        try:
            d = tomlkit.parse(text)
        except TOMLKitError:   # not TOML: its config isn't known
            d = None
        target = self._config_named(d, sc.path)
        if target != self._scenario_config(sc):
            self._check_live_robot(target)

    def _scenario_changed(self, res: scenario_validate.Result) -> None:
        """After the scenario file was written: follow its config, then take `res` as its validation."""
        if self._point_robot():
            self._revalidate()
        self._set_scenario_result(res)

    def scenario_edit(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            self._require_scenario_writable()
            made: list[Path] = []   # new track files this edit wrote; removed again unless the edit is committed
            fn = self._scenario_op(body.get("op"), body, sc.path, made)
            committed = False
            try:
                try:
                    candidate = sc.apply(fn)
                except EditError as e:
                    return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
                except (TypeError, ValueError, AttributeError, KeyError, IndexError) as e:   # malformed client values
                    return 200, {"ok": False, "errors": [{"field": "status", "message": f"bad edit: {e}"}]}
                res = scenario_validate.check(candidate, sc.path)
                refused = self._refusal(res)
                if refused is not None:
                    return 200, {"ok": False, "errors": refused}
                self._check_config_change(sc, candidate)
                try:
                    sc.commit(candidate)
                except FileChanged as e:
                    self._sync()
                    raise ApiError(409, e.message) from None
                except EditError as e:
                    return 200, {"ok": False, "errors": [{"field": e.field, "message": e.message}]}
                except OSError as e:
                    return 200, {"ok": False, "errors": [{"field": "status", "message": f"can't write the file: {e}"}]}
                committed = True
            finally:
                if not committed:
                    for f in made:
                        f.unlink(missing_ok=True)
            self._scenario_changed(res)
            return 200, {"ok": True, "version": sc.version}

    def scenario_history(self, which: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            self._require_scenario_writable()
            target = sc.peek_undo() if which == "undo" else sc.peek_redo()
            if target is None:
                return 200, {"ok": False, "errors": [{"field": "status", "message": f"nothing to {which}"}]}
            res = scenario_validate.check(target, sc.path)   # the robot may have changed since
            refused = self._refusal(res)
            if refused is not None:
                return 200, {"ok": False, "errors": [{"field": e["field"], "message": f"can't {which}: {e['message']}"}
                                                      for e in refused]}
            self._check_config_change(sc, target)
            try:
                sc.undo() if which == "undo" else sc.redo()
            except FileChanged as e:
                self._sync()
                raise ApiError(409, e.message) from None
            except OSError as e:
                raise ApiError(500, f"can't write the file: {e}") from None
            self._scenario_changed(res)
            return 200, {"ok": True, "version": sc.version}

    def robot_signals(self) -> dict:
        """signals(world) of the robot document, or {} when it doesn't load."""
        with self._lock:
            w = self._robot_world()
            if w is None:
                return {}
            if self._signals is None:
                self._signals = signals(w)
            return self._signals

    def scenario_signals(self) -> dict:
        with self._lock:
            self._need_scenario()
            return self.robot_signals()

    def scenario_configs(self) -> dict:
        with self._lock:
            sc = self._need_scenario()
            base = sc.path.parent.resolve()
        out, seen = [], set()
        for folder in (self.cwd / "configs", base):
            try:
                files = sorted(p for p in folder.glob("*.toml") if p.is_file())
            except OSError:
                continue
            for p in files:
                p = p.resolve()
                if p in seen or p == sc.path.resolve() or classify(p) != "robot":
                    continue
                seen.add(p)
                try:
                    out.append(Path(os.path.relpath(p, base)).as_posix())
                except ValueError:   # another drive (Windows)
                    out.append(p.as_posix())
        return {"configs": out}

    def world_view(self) -> dict:
        """model_view(world, include_world=True) of the robot document, or {} when it doesn't load."""
        with self._lock:
            w = self._robot_world()
            if w is None:
                return {}
            if self._scenario_view is None:
                self._scenario_view = model_view(w, include_world=True)
            return self._scenario_view

    def scenario_view(self) -> dict:
        with self._lock:
            self._need_scenario()
            return self.world_view()

    def open_scenario(self, path) -> dict:
        """Make `path` the scenario document, as if it had been given to `scrimmage edit`; the robot document
        follows its config."""
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "path must be a scenario file path")
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        if not p.is_file() or classify(p) != "scenario":
            raise ApiError(400, f"{self._display(p)} is not a scenario file")
        if self.runs.running:
            raise ApiError(409, "a scenario run is in progress")
        with self._lock:
            if self.test is not None and self.test.busy:
                raise ApiError(409, "a test run is in progress")
            try:
                doc = ConfigDoc(p)
            except OSError as e:
                raise ApiError(400, f"can't read {self._display(p)}: {e.strerror or e}") from None
            self._check_live_robot(self._scenario_config(doc))   # a live session keeps its robot document
            if self.scenario is not None:
                doc.version = self.scenario.version + 1   # the page's long poll sees the new document
            self.scenario = doc
            if self.test is not None:   # the scenario takes the test's place
                self.test.close()
                self.test = None
            if not self._point_robot() and self.robot is not None:
                self.robot.version += 1   # the same robot: its state now says scenario_open
            self._revalidate()
            self._revalidate_scenario()
        return {"ok": True}

    # -- test -------------------------------------------------------------------------------
    def wait_test_state(self, since: int | None) -> dict:
        """The test's state once its version moves from `since` (or after POLL_SECONDS); each second the files are
        read again, so an outside edit of the test or its robot shows."""
        end = time.monotonic() + POLL_SECONDS
        while True:
            with self._lock:
                self._sync()
                test = self.need_test()
            if test.wait(since, end):
                return self.need_test().state()   # another file opened meanwhile: its state (or 404)

    def test_save(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()
            test = self.need_test()
            status, out = test.save(body)
            if status == 200 and self._point_test_robot():   # the test now names another robot
                self._revalidate()
        return status, out

    def test_start(self, kind: str, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()   # the problems as the files are now
            return self.need_test().start(kind, body)

    def new_test(self, body: dict) -> dict:
        """Write `<robot stem>.test.toml` next to the robot config `robot` (default: the open robot), or
        `<stem>-2.test.toml` and so on when that is taken, with starter blocks and hooks, and open it. With
        `from_part: true` it is the Part tab's "Test it": see test_from_part."""
        if body.get("from_part") is True:
            return self.test_from_part()
        if body.get("robot") is not None:
            robot = self._user_path(body.get("robot"), "the robot config the test is for")
            if not robot.is_file() or classify(robot) != "robot":
                raise ApiError(400, f"{self._display(robot)} is not a robot config")
        else:
            with self._lock:
                robot = self.robot.path if self.mode == "robot" and self.robot is not None else None
            if robot is None:
                raise ApiError(400, "say which robot config the test is for (robot)")
        with self._lock:
            self._check_can_open()
        text = self._starter_test(robot)
        for k in range(1, 1000):
            p = robot.parent / (f"{robot.stem}.test.toml" if k == 1 else f"{robot.stem}-{k}.test.toml")
            try:
                if test_session.write_new(p, text):
                    break
            except OSError as e:
                raise ApiError(400, f"can't write {self._display(p)}: {e.strerror or e}") from None
        else:
            raise ApiError(400, f"there are too many tests of {robot.name} already; delete some")
        return self.open_path(str(p))

    def _starter_test(self, robot: Path, folder: Path | None = None) -> str:
        """A new test's text for the robot config `robot` (the test in `folder`, default the robot's): starter blocks,
        hooks and hopper from the robot's palette (a robot that doesn't load gets one wait block, and its problem
        shows once the test is open)."""
        from ..blocks.palette import palette
        from ..config import build_world, load_config
        folder = folder or robot.parent
        try:
            world = build_world(load_config(robot))
            pal, roots = palette(world, []), test_session.part_roots(world)
        except Exception:     # a robot that doesn't build: the open test says why
            pal, roots = None, {}
        return dumps_test(TestProgram(folder / "x.test.toml", Path(test_session.config_path(robot, folder)),
                                      hooks=test_session.starter_hooks(pal), blocks=test_session.starter_blocks(pal),
                                      hopper=test_session.starter_hopper(pal, roots) or None))

    def test_from_part(self) -> dict:
        """The Part tab's "Test it": make (once) `<import name>-standin.toml` next to the import's parts folder, the
        built parts on it where the CAD has them (test_session.standin_config), and `<import name>.test.toml` beside
        it with starter blocks, then open the test. Pressed again it opens the same test: nothing is made twice."""
        with self._lock:
            part = self.part
            if self.mode != "part" or not isinstance(part, part_session.PartSession):
                raise ApiError(400, "Test it works on a part made from a CAD import: open one in the Part tab")
            st, imp = part.state_and_settings()
        if st["read_only_reason"]:
            raise ApiError(409, f"the part can't be tested yet: {st['read_only_reason']}")
        if st["build"]["state"] == "building":
            raise ApiError(409, "the parts are still updating; try again when they're up to date")
        if st["build"]["state"] == "failed" or imp is None:
            raise ApiError(409, f"the parts didn't build: {st['build']['message'] or 'see the Part tab'}")
        imp_path = Path(st["path"])
        # next to the parts folder the import file is in (or beside an import file kept elsewhere)
        home = imp_path.parent.parent if imp_path.parent.name == "parts" else imp_path.parent
        parts, slots, roots = [], {}, {}
        for p in st["parts"]:
            try:
                folder = part_folder(imp, p["name"], imp_path)
            except CadError as e:
                raise ApiError(409, str(e)) from None
            marker = library.cad_marker(folder)
            if marker is None or not (folder / "part.toml").is_file():
                raise ApiError(409, f"the part {p['name']} isn't built yet; try again when the parts are up to date")
            try:
                built = load_part(folder)
                slots[marker["part"]] = [m.slot for m in built.motors]
                roots[marker["part"]] = next((b.name for b in built.bodies if b.parent is None), None)
            except (PartError, OSError) as e:
                raise ApiError(409, f"the part {p['name']} can't be read: {e}") from None
            parts.append((test_session.config_path(folder, home), marker))
        if not parts:
            raise ApiError(409, "this import makes no parts yet: put its pieces in a body first")
        with self._lock:
            self._check_can_open()
        name = sanitize(st["name"]) or "part"
        standin, test = home / f"{name}-standin.toml", home / f"{name}.test.toml"
        added: list[str] = []
        try:
            new = test_session.standin_config(parts, _chassis_root_body(), slots, roots)
            if not test_session.write_new(standin, new):
                text, added = test_session.add_to_standin(standin.read_text(encoding="utf-8"), parts,
                                                          _chassis_root_body(), slots)
                if added:
                    test_session.replace_text(standin, text)
            if not test.exists():
                test_session.write_new(test, self._starter_test(standin, home))
        except OSError as e:
            raise ApiError(400, f"can't write next to {self._display(home)}: {e.strerror or e}") from None
        except (ValueError, KeyError, TypeError) as e:   # a hand-edited stand-in that can't be read as one
            raise ApiError(409, f"can't add the new parts to {standin.name}: {e}") from None
        return {**self.open_path(str(test)), "added": added}

    # -- runs -------------------------------------------------------------------------------
    def _runs_changed(self) -> None:
        with self._runs_cond:
            self._runs_cond.notify_all()

    def run(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            sc = self._need_scenario()
            self._check_fresh(body, lambda: sc.version)
            res = self.scenario_result
            if not res.ok:
                errs = self.scenario_errors or [{"field": "status", "message": self.scenario_read_only_reason
                                            or "the scenario has errors"}]
                return 200, {"ok": False, "errors": errs}
            not_built = self._not_built()
            if not_built is not None:
                return 200, {"ok": False, "errors": [{"field": "start/not_built", "message": not_built}]}
            if self._start_problem(for_run=True) is not None:
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": UNPICKED}]}
            speed = body.get("speed")
            if speed is None:
                speed = UNSET
            else:
                try:
                    if isinstance(speed, bool) or not isinstance(speed, (str, int, float)):
                        raise ValueError(f'speed must be a positive number or "max", not {speed!r}')
                    speed = parse_speed(str(speed))
                except ValueError as e:
                    return 200, {"ok": False, "errors": [{"field": "status", "message": str(e)}]}
            path, text = sc.path, sc.text   # the run's snapshot: later edits don't change it
        try:
            run_id = self.runs.start(path, text, speed)
        except RunBusy as e:
            raise ApiError(409, str(e)) from None
        except ScenarioError as e:
            return 200, {"ok": False, "errors": [{"field": "status", "message": str(e)}]}
        return 200, {"ok": True, "id": run_id}

    def stop_run(self) -> dict:
        with self._lock:
            self._need_scenario()
        self.runs.stop()
        return {"ok": True}

    def wait_runs(self, since: int | None) -> dict:
        with self._lock:
            self._need_scenario()
        end = time.monotonic() + POLL_SECONDS
        with self._runs_cond:
            while True:
                r = self.runs.runs()
                if since is None or r["version"] != since or time.monotonic() >= end:
                    return r
                self._runs_cond.wait(1.0)

    def run_file(self, rest: str) -> Path:
        with self._lock:
            self._need_scenario()
        run_id, _, name = rest.partition("/")
        p = self.runs.file(run_id, name) if run_id and name else None
        if p is None:
            raise ApiError(404, "no such run file")
        return p

    # -- live -------------------------------------------------------------------------------
    def _live_message(self, msg: dict) -> None:
        """A message from an authenticated page on the stream (on the hub's loop)."""
        try:
            if msg.get("type") == "input":
                self.live.input(msg.get("ports"))
            elif msg.get("type") == "cmd":
                self.live.command({k: v for k, v in msg.items() if k != "type"})
        except LiveError as e:
            self.hub.publish("notice", {"kind": "error", "text": str(e)})

    def _live_client_gone(self) -> None:
        self.live.client_gone()

    def _watch_run(self, session, t: float) -> None:
        """After every robot step of a scenario run (its worker thread): frames and state for the Drive view."""
        now = time.monotonic()
        if now - self._watch_frame >= FRAME_PERIOD:
            self._watch_frame = now
            poses = session.mover_poses()
            self._watch_seq = (self._watch_seq + 1) & 0xFFFFFFFF
            self.hub.publish("frame", FRAME_HEAD.pack(self._watch_seq, SOURCE_RUN, t, len(poses))
                             + poses.astype("<f4").tobytes())
        if now - self._watch_state >= STATE_PERIOD:
            self._watch_state = now
            runs = self.runs.runs()["runs"]
            step_name = runs[0]["step_name"] if runs else None
            st = session.state()
            self.hub.publish("state", {"source": "run", "t": round(t, 4), "step_name": step_name,
                                       "mode": st["mode"], "range": st.get("range")})   # None: no test range

    def live_info(self) -> dict:
        _, _, problem = self._drivable()
        with self._lock:
            picked = self._start_problem() is None
        return {"port": self.hub.port, "status": self.live.status(), "run_active": self.runs.running,
                "can_start": problem is None, "start_problem": problem, "start_picked": picked}

    def _drivable(self):
        """(config path, config, None) when the robot document can be driven, else (None, None, why not)."""
        with self._lock:
            self._sync()
            if self.robot is None:
                return None, None, self.robot_missing
            if not self.result.ok:
                return None, None, self.robot.read_only_reason or "; ".join(e.message for e in self.result.errors)
            path, text = self.robot.path, self.robot.text
        try:
            cfg = load_config_text(text, path)
        except ConfigError as e:
            return None, None, str(e)
        if cfg.robot_program is None:
            return None, None, NO_PROGRAM
        return path, cfg, None

    def live_start(self, body: dict) -> tuple[int, dict]:
        def refuse(message: str) -> tuple[int, dict]:
            return 200, {"ok": False, "errors": [{"field": "status", "message": message}]}

        discard = body.get("discard_recording", False)
        if not isinstance(discard, bool):
            raise ApiError(400, "discard_recording must be true or false")
        # under the lock from the robot check to the start: no scenario open or edit switches the robot document
        # in between (LiveManager never calls back into the app, so holding it here can't deadlock)
        with self._lock:
            path, cfg, problem = self._drivable()
            if problem is not None:
                return refuse(problem)
            not_built = self._not_built()
            if not_built is not None:
                return 200, {"ok": False, "errors": [{"field": "start/not_built", "message": not_built}]}
            if self._start_problem() is not None:
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": UNPICKED}]}
            speed = body.get("speed", 1.0)
            if speed == "max":
                speed = None
            elif isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) \
                    or speed <= 0:
                return refuse(f'speed must be a positive number or "max", not {speed!r}')
            elif cfg.step / speed >= 1.0:
                return refuse(f"speed {speed:g} with {cfg.step:g} s steps: each step would take "
                              f"{cfg.step / speed:g} s of wall time or more, and the robot's 2 s watchdog would "
                              "release the clock")
            try:
                self.live.start(path, speed, discard_recording=discard)
            except SimBusy as e:
                raise ApiError(409, str(e)) from None
            except UnsavedRecording as e:
                return 200, {"ok": False, "errors": [{"field": "live/unsaved", "message": str(e)}]}
            except LiveError as e:
                raise ApiError(409, str(e)) from None   # already live or shutting down (the speed was checked)
        return 200, {"ok": True}

    def robot_check(self, body: dict) -> tuple[int, dict]:
        """Start and check devices: Drive's start (refused the same ways), disabled until someone enables it in
        Drive; /api/robot/devices then fills in."""
        return self.live_start({k: body[k] for k in ("speed", "discard_recording") if k in body})

    def live_stop(self) -> dict:
        self.live.stop()
        return {"ok": True}

    def save_recording(self, body: dict) -> tuple[int, dict]:
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, "path must be a file path")
        overwrite = body.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ApiError(400, "overwrite must be true or false")
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        config = self.live.status()["config"]
        if config is None:
            with self._lock:
                config = str(self.robot.path) if self.robot is not None else None
        allowed = [self.cwd] + ([Path(config).resolve().parent] if config else [])
        if not any(p.is_relative_to(base) and p != base for base in allowed):
            return 200, {"ok": False, "errors": [{"field": "status", "message":
                         f"{p} is outside the folder scrimmage edit was started in and the robot config's folder; "
                         "save the recording in one of them"}]}
        try:
            res = self.live.save_recording(p, overwrite)
        except (LiveError, RecordingError) as e:
            msg = str(e)
            field = "save/exists" if msg.endswith("already exists") else "status"
            return 200, {"ok": False, "errors": [{"field": field, "message": msg}]}
        return 200, {"ok": True, "path": res["path"], "display_path": self._display(Path(res["path"])),
                     "steps": res["steps"], "pokes": res["pokes"]}

    # -- lifecycle --------------------------------------------------------------------------
    def start(self) -> str:
        app = self

        class Handler(_Handler):
            pass
        Handler.app = app
        self._httpd = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._httpd.daemon_threads = True
        self.port = self._httpd.server_address[1]
        self.hub.start()
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True, name="assembler-http")
        self._thread.start()
        url = f"http://127.0.0.1:{self.port}/#token={self.token}"
        if self.open_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        return url

    def stop(self) -> None:
        self._fix_cancel.set()      # a running fix's program is stopped; its worker then finishes
        if self._fix_thread is not None:
            self._fix_thread.join(timeout=10)
        self.bench_jobs.stop()
        if self.bench is not None:
            self.bench.close()
        if self.part is not None:
            self.part.close()
        if self.test is not None:
            self.test.close()
        self.runs.stop()
        self.live.shutdown()
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        # no robot program from a run may outlive the editor: the run stops at once (Stop also interrupts a robot
        # start-up); a worker still busy after that gets its robot programs killed by their run tokens
        if not self.runs.join(30):
            self.runs.kill_robots()
            self.runs.join(5)
        if not self.live.join(30):
            self.live.kill_robots()
            self.live.join(5)
        self.hub.close()


class _Bounded:
    """At most `length` bytes of a request body; drain() reads and drops what is left, so an upload refused before
    its body was read (a bad name, a part that can't take it) still gets its answer: a server that closes with the
    body unread resets the connection, and the browser (on Windows above all) shows a network error instead."""

    def __init__(self, stream, length: int):
        self._stream, self.left = stream, length

    def read(self, size: int) -> bytes:
        data = self._stream.read(min(size, self.left)) if self.left > 0 else b""
        self.left -= len(data)
        return data

    def drain(self) -> None:
        try:
            while self.left > 0 and self.read(1 << 20):
                pass
        except OSError:   # the client went away: nothing to answer
            pass


class _Handler(BaseHTTPRequestHandler):
    app: App
    protocol_version = "HTTP/1.1"
    server_version = "scrimmage-assembler"

    def log_message(self, *a) -> None:
        pass

    def _send(self, status: int, payload, ctype: str = "application/json") -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload, default=str).encode()
        self.send_response(status)
        if status >= 400:   # an unread request body must not be parsed as the next request
            self.close_connection = True
            self.send_header("Connection", "close")
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _authorized(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return False
        tok = self.headers.get("X-Scrimmage-Token") or ""
        return secrets.compare_digest(tok.encode(), self.app.token.encode())

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "bad Content-Length") from None
        if n > MAX_BODY:
            raise ApiError(413, "request too large")
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise ApiError(400, "body is not JSON") from None
        if not isinstance(body, dict):
            raise ApiError(400, "body must be a JSON object")
        return body

    def _handle(self, method: str) -> None:
        url = urlparse(self.path)
        if method == "HEAD" and url.path.startswith("/api/"):
            method = "GET"
        try:
            if url.path.startswith("/api/"):
                if not self._authorized():
                    raise ApiError(403, "missing or wrong token")
                self._send(*self._api(method, url))
            elif method in ("GET", "HEAD"):
                self._static(url.path)
            else:
                raise ApiError(405, "method not allowed")
        except (ApiError, SessionError) as e:
            self._send(e.status, {"ok": False, "error": e.message, "errors": [{"field": "status", "message": e.message}]})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:   # never take the server down on a request
            logging.getLogger("scrimmage.assembler").exception("unexpected error handling %s", self.path)
            try:
                msg = f"{type(e).__name__}: {e}"
                self._send(500, {"ok": False, "error": msg, "errors": [{"field": "status", "message": msg}]})
            except OSError:
                pass

    def do_GET(self) -> None:
        self._handle("GET")

    def do_HEAD(self) -> None:
        self._handle("HEAD")

    def do_POST(self) -> None:
        self._handle("POST")

    @staticmethod
    def _since(url) -> int | None:
        since = parse_qs(url.query).get("since", [None])[0]
        try:
            return int(since) if since is not None else None
        except ValueError:
            raise ApiError(400, "since must be an integer") from None

    def _api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if path.startswith("/api/app/"):
            return self._app_api(method, url)
        if path.startswith("/api/bench/"):
            return self._bench_api(method, url, app.need_bench())
        if path == "/api/part/upload" and method == "POST":
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ApiError(400, "bad Content-Length") from None
            if n > part_session.MAX_UPLOAD:   # (not read: a body this large may be cut off)
                raise ApiError(413, "that file is too large to upload (over 512 MB)")
            body = _Bounded(self.rfile, n)
            try:
                files = None
                if self.headers.get("X-Files"):
                    try:
                        files = [(f["name"], f["size"]) for f in json.loads(unquote(self.headers["X-Files"]))]
                    except (ValueError, TypeError, KeyError):
                        raise ApiError(400, "X-Files must be a list of {name, size}") from None
                    if not files:
                        raise ApiError(400, "no files were sent")
                return app.upload(body, n, self.headers.get("X-Filename"), files,
                                  again=self.headers.get("X-Import-Again") == "1")
            finally:
                body.drain()   # a refusal answers after the body arrived, never by resetting the connection
        if path.startswith("/api/part/"):
            return self._part_api(method, url, app.need_part())
        if path.startswith("/api/test/"):
            return self._test_api(method, url)
        if path.startswith(("/api/robot/", "/api/scenario/", "/api/live/")):
            app.need_robot()
        if method == "GET":
            if path == "/api/robot/state":
                return 200, app.wait_state(self._since(url))
            if path == "/api/robot/view":
                return 200, app.view()
            if path == "/api/robot/library":
                return 200, app.library()
            if path == "/api/robot/code":
                return 200, app.code_state()
            if path == "/api/robot/code/fix":
                return 200, app.wait_fix(self._since(url))
            if path == "/api/robot/devices":
                return 200, app.live.devices(self._since(url))
            if path == "/api/scenario/state":
                return 200, app.wait_scenario_state(self._since(url))
            if path == "/api/scenario/track":
                return 200, app.scenario_track(parse_qs(url.query).get("step", [None])[0])
            if path == "/api/scenario/signals":
                return 200, app.scenario_signals()
            if path == "/api/scenario/view":
                return 200, app.scenario_view()
            if path == "/api/scenario/configs":
                return 200, app.scenario_configs()
            if path == "/api/scenario/runs":
                return 200, app.wait_runs(self._since(url))
            if path == "/api/live/info":
                return 200, app.live_info()
            if path == "/api/live/view":
                return 200, app.world_view()
            if path == "/api/live/signals":
                return 200, app.robot_signals()
            if path.startswith("/api/scenario/run/"):
                f = app.run_file(unquote(path[len("/api/scenario/run/"):]))
                return 200, f.read_bytes(), _TYPES.get(f.suffix.lower(), "application/octet-stream")
        elif method == "POST":
            body = self._body()
            if path == "/api/robot/edit":
                return app.edit(body)
            if path in ("/api/robot/undo", "/api/robot/redo"):
                return app.history(path.rsplit("/", 1)[1], body)
            if path == "/api/robot/folder":
                return 200, app.add_folder(body.get("path"))
            if path == "/api/robot/code":
                return app.code_set(body)
            if path == "/api/robot/code/fix":
                return 200, app.code_fix(body)
            if path == "/api/robot/check":
                return app.robot_check(body)
            if path == "/api/scenario/edit":
                return app.scenario_edit(body)
            if path in ("/api/scenario/undo", "/api/scenario/redo"):
                return app.scenario_history(path.rsplit("/", 1)[1], body)
            if path == "/api/scenario/run":
                return app.run(body)
            if path == "/api/scenario/stop":
                return 200, app.stop_run()
            if path == "/api/scenario/open":
                return 200, app.open_scenario(body.get("path"))
            if path == "/api/live/start":
                return app.live_start(body)
            if path == "/api/live/stop":
                return 200, app.live_stop()
            if path == "/api/live/save_recording":
                return app.save_recording(body)
        raise ApiError(404, "no such endpoint")

    def _app_api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if method == "GET" and path == "/api/app/state":
            return 200, app.app_state()
        if method == "GET" and path == "/api/app/browse":
            return 200, app.browse(parse_qs(url.query).get("dir", [None])[0])
        if method == "POST" and path == "/api/app/open":
            return 200, app.open_path(self._body().get("path"))
        if method == "POST" and path == "/api/app/new":
            return 200, app.new(self._body())
        if method == "GET" and path == "/api/app/folders":
            return 200, app.get_folders()
        if method == "POST" and path == "/api/app/folders":
            return 200, app.set_folder(self._body())
        raise ApiError(404, "no such endpoint")

    def _bench_api(self, method: str, url, bench: BenchSession) -> tuple:
        path, q = url.path, parse_qs(url.query)
        if method == "GET":
            if path == "/api/bench/state":
                return 200, bench.wait_state(self._since(url))
            if path == "/api/bench/fields":
                return 200, bench.fields()
            if path == "/api/bench/view":
                return 200, bench.view(q.get("file", [None])[0])
            if path == "/api/bench/idea":
                return 200, bench.idea(q.get("file", [None])[0])
            if path == "/api/bench/file":
                f = bench.file(q.get("path", [None])[0])
                return 200, f.read_bytes(), _TYPES.get(f.suffix.lower(), "application/octet-stream")
            if path == "/api/bench/jobs":
                return 200, self.app.bench_jobs.wait_jobs(self._since(url))
        elif method == "POST":
            body = self._body()
            if path == "/api/bench/edit":
                return bench.edit(body)
            if path in ("/api/bench/undo", "/api/bench/redo"):
                return bench.history(path.rsplit("/", 1)[1], body)
            if path == "/api/bench/new_idea":
                return bench.new_idea(body)
            if path in ("/api/bench/run", "/api/bench/sweep", "/api/bench/compare", "/api/bench/gallery"):
                return self.app.bench_jobs.submit(bench, path.rsplit("/", 1)[1], body)
        raise ApiError(404, "no such endpoint")

    def _test_api(self, method: str, url) -> tuple:
        app, path = self.app, url.path
        if method == "POST" and path == "/api/test/new":
            return 200, app.new_test(self._body())
        if method == "GET" and path == "/api/test/state":
            return 200, app.wait_test_state(self._since(url))
        test = app.need_test()
        if method == "GET" and path.startswith("/api/test/run/"):
            rest = unquote(path[len("/api/test/run/"):])
            for what, ctype in (("view", "application/json"), ("motion", "application/octet-stream")):
                if rest.endswith("/" + what):
                    try:
                        data = test.run_file(rest[:-len(what) - 1], what).read_bytes()
                    except FileNotFoundError:   # pruned meanwhile
                        raise ApiError(404, "that run is no longer kept") from None
                    return 200, data, ctype
            return 200, test.run_result(rest)
        if method == "POST":
            body = self._body()
            if path == "/api/test/save":
                return app.test_save(body)
            if path in ("/api/test/run", "/api/test/sweep", "/api/test/spread"):
                return app.test_start(path.rsplit("/", 1)[1], body)
            if path == "/api/test/cancel":
                return 200, test.cancel()
        raise ApiError(404, "no such endpoint")

    def _part_api(self, method: str, url, part) -> tuple:
        path = url.path
        if method == "GET":
            if path == "/api/part/state":
                return 200, part.wait_state(self._since(url))
            if path == "/api/part/view":
                return 200, part.view()
            if path == "/api/part/materials":
                return 200, part_session.materials()
        elif method == "POST":
            body = self._body()
            if path == "/api/part/edit":
                return part.edit(body)
            if path in ("/api/part/undo", "/api/part/redo"):
                return part.history(path.rsplit("/", 1)[1], body)
            if path == "/api/part/axis":
                return 200, part.axis(body)
            if path == "/api/part/reimport":
                return part.reimport(body)
        raise ApiError(404, "no such endpoint")

    def _static(self, urlpath: str) -> None:
        rel = "index.html" if urlpath in ("", "/") else urlpath.lstrip("/")
        root = STATIC.resolve()
        try:
            target = (root / rel).resolve()
            target.relative_to(root)
        except (ValueError, OSError):
            raise ApiError(404, "not found") from None
        if not target.is_file():
            raise ApiError(404, "not found")
        self._send(200, target.read_bytes(), _TYPES.get(target.suffix.lower(), "application/octet-stream"))
