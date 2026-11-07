"""The editor's local web server (`scrimmage edit`): static front end plus a token-protected JSON API on 127.0.0.1.

The app holds one thing at a time (`mode`):
  "robot": a robot config (the Robot tab) and, when the opened file is a scenario, that scenario (the Scenario tab);
        the Drive tab drives the robot document. With a scenario open, the robot document is the scenario's
        `config`: changing the scenario's config (an edit, undo/redo or an outside edit) loads that robot config in
        its place. A block test (`<name>.test.toml`, the Test tab; see test_session.py) attaches the same way: the
        robot document is its [test] `robot`.
  "bench": an idea session, a folder of bench files (the Bench tab; see bench_session.py).
  "part": a CAD import or a plugin-made part folder (the Part tab; see part_session.py).
  "measure": a measurement file, `<name>.measure.toml` (the Measure tab; see measure_session.py).
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
  GET  /api/app/state              -> {mode: start|robot|bench|part|measure, path, display_path, test, recent: [{path,
        kind: robot|scenario|test|measure|session|import, t}] (newest first, see recent.py), examples: [stems of examples/bench/*.toml],
        needs_folder: "cad"|"code"|null, back: {path, display_path}|null, measure_kinds: [{kind, tests: [title]}]
        (what "Measure something" offers: file.KIND_TESTS, each test by its form's title)}. back: in part mode, the robot config (or
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
  POST /api/app/new                -> body {kind: robot|scenario|session|measure|import, path, template?, robot?,
        measure?}: writes a robot config from a template (template: swerve|swerve-rev|differential), a scenario for
        the robot config `robot` (its `config` relative to the scenario's folder), a measurement file (kind measure:
        `measure` is ball|friction|launcher|cylinder, the file the starter for its first test, forms.starter_toml;
        .measure.toml is added to a name without it) or makes the session folder, then opens it as /open does. kind import ("New part from CAD"): path is the Browse-picked source (a CAD file, or a folder of STL/OBJ
        files), referenced in place; its import file goes in parts/ next to it, or in the parts/ folder next to the
        robot config `robot` when given (an existing one naming that source is opened); 400 when path isn't a CAD
        file or such a folder.
        A robot or scenario path without .toml gets it. {ok: true}; 400 for an existing path, a bad kind or
        template, or a `robot` that isn't a robot config; 409 as for open. The templates write no start_pose
        (the robot code panel asks where the robot starts). A scenario's `config` is written as `{cad}/...` (or
        `{code}/...`) when a team folder holds the robot config but not the scenario.

Settings (`/api/settings`; in every mode). The user's settings (scrimmage/settings.py), shared by every editor and the
CLI on this computer:
  GET  /api/settings[?catalog=V]   -> {units: imperial|metric, table, language, languages, catalog_version, catalog,
        theme, warning}: the
        Units setting (`scrimmage --units` wins over the file for its run), the unit table every display formats with
        (units.table_json(): {systems, kinds, units: {system: {kind: {name, factor, decimals}}}, aliases, keys,
        lengths}), the Language setting (a tag of languages; `--language` wins likewise), languages {tag: name}
        (i18n.languages(): en-US, then each catalog in scrimmage/locales/), the language's catalog (i18n.catalog_json:
        its entries that differ from the American English source; {} for en-US) and its catalog_version (a hash of
        the language and those entries; the catalog is left out when ?catalog= names the current version, so the
        page's poll doesn't carry it every time), the Theme setting
        (system|light|dark: index.html is served with it on <html data-theme>, so the first paint is in it), and why
        the settings file is being ignored, in whole or for one setting (it can't be read, isn't TOML or has a bad
        value: the defaults apply), else null. The file is read again when it changes on disk; the page polls this every few seconds to
        pick that up.
  POST /api/settings               -> body {units?, language?, theme?}: saves them in the settings file (and, under
        `--units` or `--language`, for this run too); answers as GET does. 400 for another value or none of them.

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
        always the UNCALIBRATED line (in the Language), energy_failed: true when the run's summary.json says the
        energy check failed (the simulation, not the design, is wrong; the same signal whoever ran it; ok stays as
        logged),
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
        keys set here: mass, collision, range, spring, damping, frictionloss, limit_solref)}], parts: [{name, folder, mass, com (from the last
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
        spring, damping, frictionloss, limit_solref}, motor and encoder: slot names, mounts: names), and the checklist is the
        overlay's notes.
  GET  /api/part/view              -> {pieces: [{key, vert, face}]}: each piece's mesh (left-out ones too) in robot
        coordinates (base64 little-endian float32 xyz / int32 triangles, as the robot view's meshes); cached per
        source read and robot frame. Plugin mode: one entry per body with meshes (key: the body), in the part frame.
        {pieces: []} while the source can't be read.
  GET  /api/part/materials         -> {materials: {key: {key, label, density, printed}}, motors: [names], grips:
        [[key, label, friction estimate]] (parts/grip.py's materials, for the Grip picker)}.
  POST /api/part/edit              -> body {version, op, args}: scrimmage.cad.edits.apply_edit(op, args) (set_frame
        leaves the frame unchecked unless args has confirmed = true, which "Looks right" sends; plugin
        mode: set_mass {body, kg}, set_collision {body, mode}, set_joint {body, joint: {range?, spring?, damping?,
        frictionloss?, limit_solref?}} (null removes one), set_total {kg}; piece edits are 400 "per-piece materials need the STEP:
        import it instead"). Both modes: set_grip {body, grip: a parts/grip.py material key, a number (a measured
        friction) or null (the default)} writes the body's grip in its part folder's own edits (scrimmage-edits.toml):
        plugin mode the folder's; import mode the built folder holding that body, not rebuilt (a rebuild keeps it),
        400 on body for a body built into another's or a part not built yet; both on the history.
        Each state body carries `grip` (None: the default friction). 200 with the state (its messages say what the
        edit dropped); 400 {field, message} for an edit refused (nothing written); 409 when the settings changed after
        `version` (another tab, an undo, an outside edit: a build finishing doesn't count) or the part is read-only.
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
                [robot] start_pose (x, y in m from the blue corner, rounded to 1 µm; heading in degrees, (-180, 180])
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
  GET  /api/robot/code             -> {folder, display_folder, cwd, problem, problem_text, info, checks, start,
        battery, build_failed}: the
        robot code panel. cwd is [robot_program] cwd as written (null without a [robot_program]); folder the
        absolute folder it names (display_folder relative to the working directory when under it), null when
        there is no program or a team folder isn't set. problem says why the code can't be used (an unset team
        folder, a folder that isn't there, a folder with no robot project: robotcode.detect's summary), else null;
        problem_text: the same as a finished sentence, which the page shows as it is.
        info: robotcode.ProjectInfo {folder, kind: java|robotpy, vendors, tuner_swerve, python, summary} or null;
        checks: robotcode.checklist [{id, ok: true|false|null (information), message, fix: build_agent|
        gradle_build|install_lockstep|null, detail}] ([] without info; they run programs, so this takes a moment).
        gradle_built is ok when the folder has a jar in build/libs or compiled classes under build/classes (a
        build/ folder alone isn't: a failed build leaves one); while this editor's last Build now of the folder
        failed and nothing has built there since, it is that failed result (its message and detail), and
        build_failed is true (the page words its advice by that, not by the message).
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
        picked, start/not_built, live/unsaved, 409 while a session is live (field live/already) or another simulation
        holds the lock (busy/run for a scenario run, busy/bench, busy/test)); it runs disabled
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
        top: {config, description, speed, require_lockstep, match}, steps: [{name, mode, duration, game_data,
        start_t, axes, buttons, povs, checks: [{kind, spec}], shots: [{view?, at?, width?, height?, camera?}],
        sheet}], errors, warnings, can_undo, can_redo, stations, match_keys}; each step also has inputs: the track
        CSV's path as written, or null. match: the [match] table as written ({} without one). stations and
        match_keys: driverstation.STATIONS and MATCH_KEYS, the [match] row's choices. Long-polls like /api/robot/state.
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
        match           {key, value}               a [match] key (station / fms / timer / auto_seconds /
                                                   teleop_seconds); null removes it, and the table with its last key
        game_data       {step, value}              the step's game_data (after duration); null removes it
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
        robot that doesn't load is one problem at place []), hints: [{place, message}] (palette.hints: what the
        test can run with but should know; never a reason to refuse a run), running: null|{kind: run|sweep|spread,
        progress: 0..1}, runs: list_runs (newest first; a sweep's runs have ids "sweep-NNN/NNN", a spread's
        "spread-NNN/NNN"; cancelled: Stop ended it), sweeps: [{id, target, values, cancelled, problems, rows}]
        (newest first), spreads: list_spreads ([{id, spread, placements, picked, sentence, cancelled, rows}], newest
        first), file_error: why the file as edited outside can't be read (the last good test is kept) or null,
        run_error: why the last run, sweep or spread couldn't run or null, last: {kind, id} of the last run, sweep or spread this editor made, or
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
  POST /api/test/grip              -> body {body: "<instance>.<body>", grip: a material key | a number | null}: Setup's
        grip picker writes it in that path part's folder (its scrimmage-edits.toml, as the Part tab's set_grip), and
        the robot is built again; {ok, version}. 400 (nothing written) for a grip parts/grip.py refuses, a stock
        part, a body the part doesn't have or that touches nothing, or one the robot config's own [[part]] grip sets;
        409 while a run, sweep or spread goes.
  GET  /api/test/run/<id>          -> (<id>: "004", "sweep-002/003", "spread-001/002") result.json's fields plus id,
        signals and lines (the goal and air sentences `scrimmage test` prints); /view -> view.json; /motion -> motion.bin (application/octet-stream); 404 for a run that isn't
        kept.
  POST /api/test/new               -> body {robot?, from_part?} (default: the open robot): writes <robot
        stem>.test.toml next to it (<stem>-2.test.toml ... when taken) and opens it as /api/app/open does. Its
        blocks are the starter blocks
        (test_session.starter_blocks: with a grabber on a field with rods, close its jaws at 4 V, lift with its arm,
        wait up to 10 s for a held piece and expect it held through 2 s; on a field with a station, its station_starter; else drive 0.3 m when the robot
        is a tank drive and the field has bars, then a lone hinge motor moved to 45 deg, or the first motor spun at 6 V
        for 2 s and all stopped; else wait 1 s), its hooks the robot's bodies named like a hook and its hopper those
        named like a hopper ("hopper", "intake", "indexer", or a grabber's "claw", "grabber", "gripper", "jaw"). With
        from_part: true and a part from a CAD import open ("Test it" in the Part tab): makes <import
        name>-standin.toml (the standin template carrying the built parts where the CAD has them, in front of the
        ladder range when a body is named like a hook, else facing the reef's floor coral when one is named like a
        grabber, else at the station range's chute when one is named like a hopper, else on the plain field:
        plain_field true) and <import name>.test.toml next
        to the import's parts/ folder, once each, and opens the test. Pressed again it opens the same test and adds
        the import's built parts the stand-in doesn't have yet, where the CAD has them (it never removes one);
        {ok, added: [their names], plain_field}. 409 while the parts are updating, didn't build or the part is
        read-only, 400 with no part open or in plugin mode.

Measure (`/api/measure/*`; 404 "no measurement file is open" unless one is). See measure_session.py, whose
docstring gives the file's JSON (`file`):
  GET  /api/measure/state[?since=N] -> {version, path, name, kind, tests: [{name, title, gives, need, steps, picture
        (the SVG), setup, trial: [{name, what, optional, type: reading|number|text|bool, units: [{unit, label}],
        unit (the starter's)}], value, formula, note, fit}] (the kind's tests, from forms.TEST), bases (the ball
        presets; [] for another kind), file (null when the file can't be edited), file_error (why not, or a shape
        problem; null), summary: {<test>: {error, fields: [{field, what, mean, low, high, spread, n, good, why,
        warnings, outliers, shown: {mean, low, high}, rows: [{trial, value, shown, outlier}]}]}} (the file as saved)};
        long-polls like /api/robot/state, reading the file again each second.
  POST /api/measure/summary        -> body {file}: {tests: the summary of the trials as typed}; nothing is written.
        400 for a file the JSON can't make (a bad date, a missing name); a reading problem is that test's `error`.
  POST /api/measure/fit            -> body {file, test: shot|stroke}: the fit of the trials as typed, with the
        numbers `scrimmage measure` prints: shot {what, held, value, band, band_text, rms_error, good, why, warnings,
        rows: [{measured, model, off}] (horizontal speeds, m/s; model and off null when the model ball isn't
        launched)}; stroke {conductance, band, band_text, model_time, measured_time, off (the model's time
        against the strokes' average: 0.03 is 3% slow), good, why, warnings}.
        400 with why it can't fit.
  POST /api/measure/save           -> body {file, version}: writes the file with tomlkit, changing only what changed
        (its comments stay). {ok, version}; 400 for a file the JSON can't make; 409 {error, state} when the file
        changed after `version`; 409 when the file can't be edited here (file_error).
  GET  /forms/<test>.html          -> the test's printable blank form (forms.form_html); like the static files it
        needs no token, so a plain link opens it. 404 for a test that isn't one.

Live (`/api/live/*`; one simulation at a time: a drive session, a scenario run, a bench job or a test run):
  GET  /api/live/info              -> {port, status, run_active, can_start, start_problem, start_picked,
        real_ds_default}: port is the stream's
        WebSocket port; status is the session's {state: idle|starting|running|paused|stopped|error, error, config,
        t, mode, chosen_mode, enabled, paused, speed, speed_locked, recording, pokes, can_save, match, stations} (mode
        is the effective mode, "disabled" while not enabled; chosen_mode is the one Enable runs; match is the
        session's {station, fms, timer, auto_seconds, teleop_seconds, game_data} with game_data the Drive tab's box,
        null before the session runs; stations lists the six); run_active is true while a
        scenario run is in progress; can_start is false when the robot document can't be driven (it doesn't load
        or has no [robot_program]), with start_problem saying why in Start's own words (else null). start_picked
        is false while a [[part]] robot has neither [robot] start_pose nor [range] start (Start then asks).
        real_ds_default is the robot config's [driver_station] real (false when it doesn't load): where the Drive
        tab's Driver Station choice starts. status.real_ds is true while (and after) a session the FRC Driver Station
        app drives; its mode, enabled and match then come from the robot's own reports (real_ds_report), and
        status.driver_station is "connected" or "waiting for the Driver Station", and status.match.silent is true
        while connected but no DS packet has come for over 1 s.
  GET  /api/live/view              -> model_view(world, include_world=True) of the robot document (movers and
        movable included; frames list poses in its movers order), or {} when the robot config doesn't load.
  GET  /api/live/signals           -> signals(world) of the robot document, or {}.
  POST /api/live/start             -> body {speed?, discard_recording?, real_ds?} (speed: a positive number,
        default 1, or "max"; real_ds: true when the FRC Driver Station app drives, null or absent for the config's
        [driver_station] real): drives the robot document's config. While the real DS drives, the stream's mode,
        enable, disable, match, pause, resume, step and record_start commands and its inputs are refused with a
        notice saying why. {ok: true}; 200 {ok: false, errors: [{field: "status",
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
with that port and its Origin the page's own (http://127.0.0.1:<http port> or http://localhost:<http port>), or it
is refused with HTTP 403 (and with 503 while 16 sockets already wait for their token); then the first message must
be {"token": "<session token>"} within 5 s, or the socket is closed (code 1008) with nothing sent. Then the server
sends:
  binary frames, little-endian packed: u32 seq, u8 source (0 live, 1 run), f64 t, u16 n, then n x 7 float32
        (x, y, z, qw, qx, qy, qz) per mover in the view's movers order; at most one per 33 ms per source.
  {"type": "state", "source": "live", ...status, readouts, signals, console (last 200 lines), console_seq} and,
        during a scenario run, {"type": "state", "source": "run", t, step_name, mode, range (the score counts or null)}; at most every 100 ms, each a
        full snapshot (latest wins: a slow page may miss intermediate ones).
  {"type": "notice", kind, text}: e.g. a refused command (queued; none are dropped while the page keeps up).
The page sends {"type": "input", "ports": {"<port>": {axes, buttons, povs}}} and {"type": "cmd", "cmd": ...}
(mode, enable, disable, pause, resume, step, speed, set_pose, push, move_body, reset, record_start, record_stop,
signals, match: any of station, fms, timer, auto_seconds, teleop_seconds and game_data, the box that reaches the robot
while it is enabled in Teleop); a bad command comes back as an error notice, malformed messages (JSON nested too deeply to parse among
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
import logging
import math
import os
import secrets
import threading
import time
import tomllib
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path

import tomlkit
from tomlkit.exceptions import TOMLKitError

from .. import i18n, robotcode, teamfolders
from ..config import ConfigError, load_config_text
from ..driverstation import MATCH_KEYS, STATIONS
from ..lockstep import parse_speed
from ..parts.model import PartError, load_part
from ..scenario import ScenarioError
from ..scenario_runner import UNSET
from ..testrange.spec import BUILTIN_RANGES, RangeError, load_range
from ..tracks import TrackError, load_track
from ..cad import CadError
from ..cad.build import part_folder
from ..cad.names import sanitize
from . import edits, library, part_session, recent, scenario_edits, scenario_validate, test_session, validate
from .app_files import FilesMixin
from .app_attach import AttachMixin
from .app_robot import RobotMixin
from .bench_jobs import BenchJobs
from .bench_session import BenchSession
from .doc import ConfigDoc, FileChanged
from .edits import EditError
from .http_handler import _Handler
from .live import FRAME_HEAD, AlreadyLive, LiveError, LiveManager, RecordingTaken, UnsavedRecording
from .recorder import RecordingError, RecordingExists
from .runs import RunBusy, RunManager
from .server_common import (POLL_SECONDS, FRAME_PERIOD, STATE_PERIOD, ApiError, _nothing_to, _cant, _sentence,
                            _chassis_root_body, _fe, _is_num, _no_program, _unpicked, _not_built, start_sentence,
                            classify, _info_json, remember_opened)
from .signals import signals
from .simlock import SimBusy, SimLock
from .stream import StreamHub
from .measure_session import MeasureSession
from .test_session import TestSession
from .view import model_view
from ..blocks.program import Program as TestProgram, dumps as dumps_test

# The 404s while the open file's kind doesn't match the route (N_(): said with translated() when raised)
NO_ROBOT = i18n.N_("no robot is open")
NO_SESSION = i18n.N_("no idea session is open")
NO_PART = i18n.N_("no part is open")
NO_TEST = i18n.N_("no test is open")
NO_MEASURE = i18n.N_("no measurement file is open")
FIXES = ("build_agent", "gradle_build", "install_lockstep")
FIX_LINES = 1000          # a fix's output lines kept for the page (the last ones)
BATTERY_DEFAULTS = {"nominal_voltage": 12.0, "resistance": 0.02}   # world.py's, when [battery] leaves one out
SOURCE_RUN = 1


class App(FilesMixin, AttachMixin, RobotMixin):
    def __init__(self, path: Path | None, open_browser: bool = True, port: int = 0,
                 runs_dir: Path | str | None = None, parts_dir: Path | None = None):
        self.cwd = Path.cwd().resolve()
        self.open_browser = open_browser
        self.port = port
        self.token = secrets.token_urlsafe(24)
        self.extra_folders: list[Path] = []
        self.robot: ConfigDoc | None = None
        self.robot_missing: str | None = None     # why there is no robot document (scenario mode only)
        self._missing_words = None                # ... a function wording it again (a Language change), or None
        self._missing_lang: str | None = None     # ... and the language robot_missing is in
        self._robot_target: Path | None = None    # the robot config the scenario names
        self._robot_ver = 0                       # the robot version while there is no robot document
        self.result: validate.Result | None = None
        self._robot_lang: str | None = None       # the language result and the robot's read-only reason are in
        self.scenario: ConfigDoc | None = None
        self.scenario_result: scenario_validate.Result | None = None
        self.scenario_read_only_reason: str | None = None
        self.scenario_robot_problem: str | None = None
        self.scenario_errors: list[dict] = []
        self._scenario_lang: str | None = None    # the language scenario_errors and the read-only reason are in
        self.test: TestSession | None = None      # the block test (robot mode; its robot is the robot document)
        self.bench: BenchSession | None = None    # the idea session (bench mode)
        self.part = None                          # the PartSession or PluginSession (part mode)
        self.measure: MeasureSession | None = None   # the measurement file (measure mode)
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
        self._fix_again = None    # a failed fix's result worded again in the Language now (robotcode._failed), or None
        self._fix_thread: threading.Thread | None = None
        self._fix_cancel = threading.Event()
        # code folder -> (when, the failed gradle_built check, a function wording it again: robotcode._failed)
        self._build_failed: dict[str, tuple] = {}
        self._ranges: dict = {}   # (range file, its mtime) -> its named starts
        with self._lock:
            self._load(Path(path) if path is not None else None, initial=True, parts_dir=parts_dir)

    # -- what is open -----------------------------------------------------------------------
    @property
    def mode(self) -> str:
        """"bench" (an idea session), "part" (a CAD import or plugin part folder), "measure" (a measurement file),
        "robot" (a robot config, a scenario or a test) or "start" (nothing)."""
        if self.bench is not None:
            return "bench"
        if self.part is not None:
            return "part"
        if self.measure is not None:
            return "measure"
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
                                          self.test.version if self.test else 0,
                                          self.measure.version if self.measure else 0)
        doc = ConfigDoc(path) if kind in ("robot", "scenario") else None
        test = TestSession(path, self.simlock, floor) if kind == "test" else None   # TestFileError: nothing replaced
        measure = MeasureSession(path, floor) if kind == "measure" else None
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
        self._missing_words = None
        self.scenario, self.extra_folders = None, []
        old_test, self.test = self.test, test
        old_measure, self.measure = self.measure, measure
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
        if old_measure is not None:
            old_measure.close()

    def need_robot(self) -> None:
        """404 unless a robot config or scenario is open (the Robot, Scenario and Drive tabs' routes)."""
        with self._lock:
            if self.mode != "robot":
                raise ApiError(404, i18n.translated(NO_ROBOT))

    def need_test(self) -> TestSession:
        with self._lock:
            if self.test is None:
                raise ApiError(404, i18n.translated(NO_TEST))
            return self.test

    def need_measure(self) -> MeasureSession:
        with self._lock:
            if self.measure is None:
                raise ApiError(404, i18n.translated(NO_MEASURE))
            return self.measure

    def need_bench(self) -> BenchSession:
        with self._lock:
            if self.bench is None:
                raise ApiError(404, i18n.translated(NO_SESSION))
            return self.bench

    def need_part(self):
        with self._lock:
            if self.part is None:
                raise ApiError(404, i18n.translated(NO_PART))
            return self.part

    def remember_part(self) -> None:
        """The open part in the recent list: its import file, or the plugin part folder."""
        with self._lock:
            p = self.part.path if self.part is not None else None
        if p is not None:
            recent.remember(p, "import")

    def _user_path(self, path, what: str) -> Path:
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, i18n.t("path must be {what}", what=what))
        p = Path(path).expanduser()
        return (p if p.is_absolute() else self.cwd / p).resolve()

    def _check_can_open(self) -> None:
        """409 while a drive session or a scenario run is live: they keep the robot document they started with."""
        if self.live.status()["state"] in ("starting", "running", "paused"):
            raise ApiError(409, i18n.t("a drive session is live; stop it first"))
        if self.runs.running:
            raise ApiError(409, i18n.t("a scenario run is in progress; stop it first"))
        if self.bench_jobs.active:   # this App's own job; another process holding the session lock never refuses
            raise ApiError(409, i18n.t("a bench run is in progress; wait for it to finish"))
        if self.test is not None and self.test.busy:
            raise ApiError(409, i18n.t("a test run is in progress; stop it first"))

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

    def _program_folder(self, sentence: bool = False) -> tuple[str | None, Path | None, str | None]:
        """(the [robot_program] cwd as written, the folder it names, why it can't be used) for the robot document;
        all None without a [robot_program]. With `sentence`, why is a finished sentence (the robot code panel shows
        it as it is). Called with self._lock held."""
        raw = self.robot.doc.unwrap() if self.robot is not None and self.robot.doc is not None else {}
        rp = raw.get("robot_program")
        if not isinstance(rp, dict):
            return None, None, None
        cwd = rp.get("cwd", ".")
        if not isinstance(cwd, str):
            return None, None, (i18n.t("{robot_program_table} cwd must be a folder path, not {cwd}.", cwd=repr(cwd),
                                       robot_program_table="[robot_program]") if sentence
                                else i18n.t("{robot_program_table} cwd must be a folder path, not {cwd}",
                                            cwd=repr(cwd), robot_program_table="[robot_program]"))
        try:
            folder = (self.robot.path.parent / teamfolders.expand(cwd)).resolve()
        except teamfolders.TeamFolderError as e:
            why = str(e)    # teamfolders' words (the loader's too), not translated: made a sentence here
            return cwd, None, (why[:1].upper() + why[1:] + ("" if why.endswith(".") else ".")) if sentence else why
        if not folder.is_dir():
            if sentence:
                return cwd, folder, i18n.t("The robot code folder {path} isn't there (was it moved?): pick it again.",
                                           path=self._display(folder))
            return cwd, folder, i18n.t("the robot code folder {path} isn't there (was it moved?): pick it again",
                                       path=self._display(folder))
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
            sentence = (i18n.t("Starts at {name}", name=named) if named is not None else i18n.t("Not picked yet") if not picked
                        else i18n.t("Starts as written"))
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
        """_unpicked() while the robot document (a [[part]] robot) has neither [robot] start_pose nor [range] start
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
        return _unpicked()

    def code_state(self) -> dict:
        with self._lock:
            self._sync()
            cwd, folder, problem = self._program_folder()
            out = {"folder": str(folder) if folder is not None else None,
                   "display_folder": self._display(folder) if folder is not None else None,
                   "cwd": cwd, "problem": problem, "problem_text": self._program_folder(sentence=True)[2], "info": None,
                   "checks": [], "start": self._start(), "battery": self._battery(), "build_failed": False}
        if folder is not None and problem is None:   # outside the lock: the checks run programs
            info = robotcode.detect(folder)
            if info.kind is None:
                out["problem"] = info.summary
                out["problem_text"] = i18n.t("Nothing here scrimmage can start: no build.gradle or robot.py.")
            else:
                out["info"] = _info_json(info)
                failed = self._failed_build(folder)
                out["build_failed"] = failed is not None
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
        if built is not None and built >= got[0]:
            return None
        return dataclasses.asdict(got[2]()) if got[2] is not None else got[1]   # in the Language now

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
            return _not_built("running")
        if self._failed_build(folder) is not None:
            return _not_built("failed")
        if robotcode.built_at(folder) is None:
            return _not_built("never")
        return None

    def code_set(self, body: dict) -> tuple[int, dict]:
        """Point the robot at its code: detect the project in `folder` and write [robot_program] (one undo step). A
        refusal's first error carries problem_text: the refusal as a finished sentence, which the page shows as it
        is."""
        try:
            status, out = self._code_set(body)
        except ApiError as e:
            e.detail = {**getattr(e, "detail", {}), "problem_text": _sentence(e.message)}
            raise
        errors = out.get("errors") or []
        if not out.get("ok") and errors:
            errors[0] = {**errors[0], "problem_text": _sentence("; ".join(e["message"] for e in errors))}
        return status, out

    def _code_set(self, body: dict) -> tuple[int, dict]:
        p = self._user_path(body.get("folder"), "a folder path")
        if not p.is_dir():
            raise ApiError(400, i18n.t("{path} is not a folder", path=self._display(p)))
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

    def _fix_json(self) -> dict:
        """The fix's state; a failed one's result in the Language now (its lines are the program's own). Fix lock
        held."""
        out = {**self._fix, "lines": list(self._fix["lines"])}
        if self._fix_again is not None and not out["running"]:
            out["result"] = dataclasses.asdict(self._fix_again())
        return out

    def fix_state(self) -> dict:
        with self._fix_cond:
            return self._fix_json()

    def wait_fix(self, since: int | None) -> dict:
        end = time.monotonic() + POLL_SECONDS
        with self._fix_cond:
            while since is not None and self._fix["version"] == since and time.monotonic() < end:
                self._fix_cond.wait(1.0)
            return self._fix_json()

    def code_fix(self, body: dict) -> dict:
        """Run one readiness fix (robotcode.run_fix) on the fix worker: one at a time."""
        fix = body.get("fix")
        if fix not in FIXES:
            raise ApiError(400, i18n.t("fix must be one of {choices}", choices=', '.join(FIXES)))
        with self._lock:
            _, folder, problem = self._program_folder()
        if folder is None or problem is not None:
            raise ApiError(400, problem or i18n.t("pick the robot code's folder first"))
        info = robotcode.detect(folder)
        if info.kind is None:
            raise ApiError(400, info.summary)
        with self._fix_cond:
            if self._fix["running"]:
                raise ApiError(409, i18n.t("a fix is already running; wait for it to finish"))
            cancel = self._fix_cancel = threading.Event()
            f = self._fix
            f.update(id=f["id"] + 1, fix=fix, running=True, lines=[], result=None, version=f["version"] + 1)
            self._fix_again = None
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
                            self._build_failed[str(info.folder)] = (time.time(), dataclasses.asdict(result),
                                                                    getattr(result, "again", None))
                    self._fix_again = getattr(result, "again", None)
                    self._fix.update(running=False, result=dataclasses.asdict(result),
                                     version=self._fix["version"] + 1)
                    self._fix_cond.notify_all()
            self._fix_thread = threading.Thread(target=work, daemon=True, name="robot-code-fix")
            self._fix_thread.start()
        return {"ok": True, "id": fix_id}

    # -- scenario ---------------------------------------------------------------------------
    def _need_scenario(self) -> ConfigDoc:
        if self.scenario is None:
            raise ApiError(404, i18n.t("no scenario is open (scrimmage edit was given a robot config)"))
        return self.scenario

    def scenario_state(self) -> dict:
        sc = self._need_scenario()
        res = self.scenario_result
        if self._scenario_lang != i18n.current_language():
            # the Language setting changed since: the errors again, in its words (the file is the same), the reason
            # no robot is attached too
            self._reword_missing()
            self._set_scenario_result(scenario_validate.check(sc.text, sc.path))
            res = self.scenario_result
        raw = sc.doc.unwrap() if sc.doc is not None else {}
        top = {k: raw.get(k) for k in ("config", "description", "speed", "require_lockstep")}
        top["match"] = raw["match"] if isinstance(raw.get("match"), dict) else {}
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
            steps.append({"name": s.get("name"), "mode": s.get("mode"), "duration": s.get("duration"),
                          "game_data": s.get("game_data"), "start_t": t,
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
                "can_undo": ro is None and sc.can_undo, "can_redo": ro is None and sc.can_redo,
                "stations": list(STATIONS), "match_keys": list(MATCH_KEYS)}

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
                raise ApiError(400, i18n.t("step must be an integer")) from None
            s = steps[i] if 0 <= i < len(steps) and isinstance(steps[i], dict) else None
            if s is None or not isinstance(s.get("inputs"), str):
                raise ApiError(404, i18n.t("step {step} has no input track", step=i))
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
            "match": lambda d: e.set_match(d, b.get("key"), b.get("value")),
            "game_data": lambda d: e.set_step_game_data(d, b.get("step"), b.get("value")),
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
            raise ApiError(400, i18n.t("unknown op {op} ({choices})", op=repr(op), choices=', '.join(ops)))
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
                    return 200, {"ok": False, "errors": [{"field": "status", "message": i18n.t("can't write the file: {why}",
                                                                                               why=e)}]}
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
                return 200, {"ok": False, "errors": [{"field": "status", "message": _nothing_to(which)}]}
            res = scenario_validate.check(target, sc.path)   # the robot may have changed since
            refused = self._refusal(res)
            if refused is not None:
                return 200, {"ok": False, "errors": [{"field": e["field"], "message": _cant(which, e["message"])}
                                                      for e in refused]}
            self._check_config_change(sc, target)
            try:
                sc.undo() if which == "undo" else sc.redo()
            except FileChanged as e:
                self._sync()
                raise ApiError(409, e.message) from None
            except OSError as e:
                raise ApiError(500, i18n.t("can't write the file: {why}", why=e)) from None
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
            raise ApiError(400, i18n.t("path must be a scenario file path"))
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        if not p.is_file() or classify(p) != "scenario":
            raise ApiError(400, i18n.t("{path} is not a scenario file", path=self._display(p)))
        if self.runs.running:
            raise ApiError(409, i18n.t("a scenario run is in progress"))
        with self._lock:
            if self.test is not None and self.test.busy:
                raise ApiError(409, i18n.t("a test run is in progress"))
            try:
                doc = ConfigDoc(p)
            except OSError as e:
                raise ApiError(400, i18n.t("can't read {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
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

    # -- measure ----------------------------------------------------------------------------
    def wait_measure_state(self, since: int | None) -> dict:
        """The measurement file's state once its version moves from `since` (or after POLL_SECONDS); each second the
        file is read again, so an outside edit shows."""
        end = time.monotonic() + POLL_SECONDS
        while True:
            measure = self.need_measure()
            measure.reload_if_changed()
            if measure.wait(since, end):
                return self.need_measure().state()   # another file opened meanwhile: its state (or 404)

    def test_save(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()
            test = self.need_test()
            status, out = test.save(body)
            if status == 200 and self._point_test_robot():   # the test now names another robot
                self._revalidate()
        return status, out

    def test_grip(self, body: dict) -> tuple[int, dict]:
        with self._lock:
            self._sync()
            test = self.need_test()
            status, out = test.grip(body)
            self._sync()    # the part folder changed: the robot is built again, and the test's palette with it
            return status, {**out, "version": test.version}

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
                raise ApiError(400, i18n.t("{path} is not a robot config", path=self._display(robot)))
        else:
            with self._lock:
                robot = self.robot.path if self.mode == "robot" and self.robot is not None else None
            if robot is None:
                raise ApiError(400, i18n.t("say which robot config the test is for (robot)"))
        with self._lock:
            self._check_can_open()
        text = self._starter_test(robot)
        for k in range(1, 1000):
            p = robot.parent / (f"{robot.stem}.test.toml" if k == 1 else f"{robot.stem}-{k}.test.toml")
            try:
                if test_session.write_new(p, text):
                    break
            except OSError as e:
                raise ApiError(400, i18n.t("can't write {path}: {why}", path=self._display(p), why=e.strerror or e)) from None
        else:
            raise ApiError(400, i18n.t("there are too many tests of {name} already; delete some", name=robot.name))
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
        it with starter blocks, then open the test. Pressed again it opens the same test: nothing is made twice.
        `plain_field` is true when it made the stand-in on the plain field (no body like a hook or a hopper picked a
        field for it), so the page can say where it starts."""
        with self._lock:
            part = self.part
            if self.mode != "part" or not isinstance(part, part_session.PartSession):
                raise ApiError(400, i18n.t("Test it works on a part made from a CAD import: open one in the Part tab"))
            st, imp = part.state_and_settings()
        if st["read_only_reason"]:
            raise ApiError(409, i18n.t("the part can't be tested yet: {why}", why=st["read_only_reason"]))
        if st["build"]["state"] == "building":
            raise ApiError(409, i18n.t("the parts are still updating; try again when they're up to date"))
        if st["build"]["state"] == "failed" or imp is None:
            raise ApiError(409, i18n.t("the parts didn't build: {why}",
                                       why=st["build"]["message"] or i18n.t("see the Part tab")))
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
                raise ApiError(409, i18n.t("the part {name} isn't built yet; try again when the parts are up to date",
                                           name=p["name"]))
            try:
                built = load_part(folder)
                slots[marker["part"]] = [m.slot for m in built.motors]
                roots[marker["part"]] = next((b.name for b in built.bodies if b.parent is None), None)
            except (PartError, OSError) as e:
                raise ApiError(409, i18n.t("the part {name} can't be read: {why}", name=p["name"], why=e)) from None
            parts.append((test_session.config_path(folder, home), marker))
        if not parts:
            raise ApiError(409, i18n.t("this import makes no parts yet: put its pieces in a body first"))
        with self._lock:
            self._check_can_open()
        name = sanitize(st["name"]) or "part"
        standin, test = home / f"{name}-standin.toml", home / f"{name}.test.toml"
        added: list[str] = []
        plain = False     # a new stand-in on the plain field: no body is like a hook or a hopper (the page says so)
        try:
            new = test_session.standin_config(parts, _chassis_root_body(), slots, roots)
            if test_session.write_new(standin, new):
                plain = "range" not in tomllib.loads(new)
            else:
                text, added = test_session.add_to_standin(standin.read_text(encoding="utf-8"), parts,
                                                          _chassis_root_body(), slots)
                if added:
                    test_session.replace_text(standin, text)
            if not test.exists():
                test_session.write_new(test, self._starter_test(standin, home))
        except OSError as e:
            raise ApiError(400, i18n.t("can't write next to {path}: {why}", path=self._display(home),
                                       why=e.strerror or e)) from None
        except (ValueError, KeyError, TypeError) as e:   # a hand-edited stand-in that can't be read as one
            raise ApiError(409, i18n.t("can't add the new parts to {name}: {why}", name=standin.name, why=e)) from None
        return {**self.open_path(str(test)), "added": added, "plain_field": plain}

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
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": _unpicked()}]}
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
            raise ApiError(404, i18n.t("no such run file"))
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
        _, cfg, problem = self._drivable()
        with self._lock:
            picked = self._start_problem() is None
        return {"port": self.hub.port, "status": self.live.status(), "run_active": self.runs.running,
                "can_start": problem is None, "start_problem": problem, "start_picked": picked,
                "real_ds_default": cfg is not None and cfg.real_ds}

    def _drivable(self):
        """(config path, config, None) when the robot document can be driven, else (None, None, why not)."""
        with self._lock:
            self._sync()
            self._robot_words()
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
            return None, None, _no_program()
        return path, cfg, None

    def live_start(self, body: dict) -> tuple[int, dict]:
        def refuse(message: str) -> tuple[int, dict]:
            return 200, {"ok": False, "errors": [{"field": "status", "message": message}]}

        discard = body.get("discard_recording", False)
        if not isinstance(discard, bool):
            raise ApiError(400, i18n.t("discard_recording must be true or false"))
        real_ds = body.get("real_ds")
        if real_ds is not None and not isinstance(real_ds, bool):
            raise ApiError(400, i18n.t("real_ds must be true, false or null"))
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
                return 200, {"ok": False, "errors": [{"field": "start/unpicked", "message": _unpicked()}]}
            speed = body.get("speed", 1.0)
            if speed == "max":
                speed = None
            elif isinstance(speed, bool) or not isinstance(speed, (int, float)) or not math.isfinite(speed) \
                    or speed <= 0:
                return refuse(i18n.t("speed must be a positive number or \"{max}\", not {speed}", speed=repr(speed),
                                     max="max"))
            elif cfg.step / speed >= 1.0:
                return refuse(i18n.t("speed {speed} with {step} s steps: each step would take {wall} s of wall time or more, "
                                     "and the robot's 2 s watchdog would release the clock", speed=f"{speed:g}",
                                     step=f"{cfg.step:g}", wall=f"{cfg.step / speed:g}"))
            try:
                self.live.start(path, speed, discard_recording=discard, real_ds=real_ds)
            except SimBusy as e:
                raise ApiError(409, str(e), field=f"busy/{e.holder}") from None     # busy/run: a scenario run
            except UnsavedRecording as e:
                return 200, {"ok": False, "errors": [{"field": "live/unsaved", "message": str(e)}]}
            except AlreadyLive as e:
                raise ApiError(409, str(e), field="live/already") from None
            except LiveError as e:
                raise ApiError(409, str(e)) from None   # shutting down (the speed was checked)
        return 200, {"ok": True}

    def robot_check(self, body: dict) -> tuple[int, dict]:
        """Start and check devices: Drive's start (refused the same ways), disabled until someone enables it in
        Drive; /api/robot/devices then fills in."""
        return self.live_start({k: body[k] for k in ("speed", "discard_recording", "real_ds") if k in body})

    def live_stop(self) -> dict:
        self.live.stop()
        return {"ok": True}

    def save_recording(self, body: dict) -> tuple[int, dict]:
        path = body.get("path")
        if not isinstance(path, str) or not path.strip():
            raise ApiError(400, i18n.t("path must be a file path"))
        overwrite = body.get("overwrite", False)
        if not isinstance(overwrite, bool):
            raise ApiError(400, i18n.t("overwrite must be true or false"))
        p = Path(path).expanduser()
        p = (p if p.is_absolute() else self.cwd / p).resolve()
        config = self.live.status()["config"]
        if config is None:
            with self._lock:
                config = str(self.robot.path) if self.robot is not None else None
        allowed = [self.cwd] + ([Path(config).resolve().parent] if config else [])
        if not any(p.is_relative_to(base) and p != base for base in allowed):
            return 200, {"ok": False, "errors": [{"field": "status", "message": i18n.t(
                         "{path} is outside the folder scrimmage edit was started in and the robot config's folder; "
                         "save the recording in one of them", path=p)}]}
        try:
            res = self.live.save_recording(p, overwrite)
        except (LiveError, RecordingError) as e:
            field = "save/exists" if isinstance(e, (RecordingExists, RecordingTaken)) else "status"
            return 200, {"ok": False, "errors": [{"field": field, "message": str(e)}]}
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
