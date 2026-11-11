"""Shared by the CAD tests."""
from pathlib import Path

# FIRST's 2024 field CAD (88 MB) isn't committed; tests that read it skip when it is missing.
FIELD_STEP = Path("/tmp/claude-1000/-home-altainia-code-scrimmage/3537f469-7b90-4632-aa74-f3b52c0bc63a/scratchpad/kickoff/work/cad/field/FE-2024.STEP")
