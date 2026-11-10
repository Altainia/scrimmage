# Task 1 report: STEP pieces keep their product names

## Changed
- scrimmage/cad/source.py: `NAUO_NAME`, `_instance_names(raw)` (one `_ENTITY` pass; keeps NEXT_ASSEMBLY_USAGE_OCCURRENCE,
  PRODUCT_DEFINITION, PRODUCT_DEFINITION_FORMATION[_WITH_SPECIFIED_SOURCE] and PRODUCT, matched by exact name before `(`;
  PRODUCT name, else its id). `_load_step` reads the file's bytes only when a GLB node name matches `NAUO_NAME`;
  `walk` swaps a NAUO name for `names.get(name, name)` before `_numbered`.
- tests/make_cad_fixtures.py: FreeCAD imports guarded; FreeCAD-only work under `if App is not None`; new
  `make_nauo_fixture()` runs without FreeCAD (`python tests/make_cad_fixtures.py`).
- tests/data/cad/climber_nauo.step: new (13 instances rewritten to `NAUO<n>`).
- tests/cad_helpers.py: `FIELD_STEP`.
- tests/test_cad_source.py: the 4 tests from the brief.

## Tests
tests/test_cad_*.py: 154 passed (field test ran, not skipped, 5507 pieces, no NAUO segments, `Boost Area End` found).
Full suite: `4624 passed, 7 skipped, 5 xfailed in 2405.85s (0:40:05)`. No flake.

## Decisions / concerns
- The brief says keep only NAUO and PRODUCT bodies, but the chain needs PRODUCT_DEFINITION and the formation too; I keep all four.
- Blank node name keeps `node<idx>` (R1). A NAUO with no resolvable product keeps its NAUO label.
- Unreadable file on the second read is ignored (names stay NAUO).
- No concerns.
