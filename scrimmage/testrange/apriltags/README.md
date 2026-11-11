# AprilTag layouts

These files are WPILib's `AprilTagFieldLayout` JSON, copied byte for byte (not edited):

- Source: https://github.com/wpilibsuite/allwpilib, tag `v2026.2.2`
- Commit: `7ca35e5678cf32caec6a1a866ca51d0136c4c398` (the tag object is `1fd159938f65ee9d0b22c8a369030b68e0efa82c`)
- Path of each file: `apriltag/src/main/native/resources/edu/wpi/first/apriltag/<name>.json`
- `LICENSE.md` is allwpilib's root `LICENSE.md` at that tag (BSD 3-clause), as is.

| File | Git blob sha |
| --- | --- |
| `2024-crescendo.json` | `8cb837a5023bc3e3b32ad7d79dfb4c7c37405151` (the same blob as at `v2024.3.2`) |
| `2026-rebuilt-welded.json` | `8c5a52de874a12a50c9881919ed09c1771114abf` |
| `2026-rebuilt-andymark.json` | `ecc03907dab8f378b18602d2de37a01f378c1483` |

(`git hash-object <file>` prints the sha.)

## Updating

1. Pick the new allwpilib tag. Download the same three paths and `LICENSE.md` from
   `https://raw.githubusercontent.com/wpilibsuite/allwpilib/<tag>/...` into an empty folder first.
2. Check each file's `git hash-object`, then copy them here unchanged.
3. Change the tag, commit and shas above. The commit is the tag's commit (`git rev-parse <tag>^{commit}`), not the
   tag object's sha.
4. Run `tests/test_range_apriltags.py`: a layout's field must stay within 5 cm of the range it is used on.
