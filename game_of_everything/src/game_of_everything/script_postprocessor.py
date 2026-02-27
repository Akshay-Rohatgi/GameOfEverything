"""
script_postprocessor.py
-----------------------
Extensible post-processing pipeline for the final concatenated deployment script.

Each processor is a plain function: (str) -> str.
Add new processors to SCRIPT_POST_PROCESSORS in the order they should run.
"""

from __future__ import annotations

import re
from typing import Callable

# ---------------------------------------------------------------------------
# Individual processors
# ---------------------------------------------------------------------------

def inject_shebang(script: str) -> str:
    """Remove any existing shebang lines and prepend exactly one #!/bin/bash."""
    lines = script.splitlines()
    # Strip every line that looks like a shebang, wherever it appears
    cleaned = [line for line in lines if not line.startswith("#!")]
    return "#!/bin/bash\n" + "\n".join(cleaned)


def ensure_set_e(script: str) -> str:
    """Ensure 'set -e' appears immediately after the shebang line.

    Fails the script fast on any non-zero exit code, which is useful for
    lab setup scripts where a silent failure would produce a broken environment.
    """
    lines = script.splitlines()
    if not lines:
        return script

    # Find the shebang (should always be line 0 after inject_shebang)
    insert_at = 1 if lines[0].startswith("#!") else 0

    # Remove any existing 'set -e' / 'set -o errexit' lines to avoid duplicates
    cleaned = [
        line for line in lines
        if line.strip() not in ("set -e", "set -o errexit")
    ]

    cleaned.insert(insert_at, "set -e")
    return "\n".join(cleaned)


def normalize_blank_lines(script: str) -> str:
    """Collapse runs of more than two consecutive blank lines into two."""
    return re.sub(r"\n{3,}", "\n\n", script)


# ---------------------------------------------------------------------------
# The pipeline — processors run left-to-right
# ---------------------------------------------------------------------------

SCRIPT_POST_PROCESSORS: list[Callable[[str], str]] = [
    inject_shebang,
    ensure_set_e,
    normalize_blank_lines,
]


def apply_post_processors(script: str) -> str:
    """Run the script through every processor in SCRIPT_POST_PROCESSORS."""
    for processor in SCRIPT_POST_PROCESSORS:
        script = processor(script)
    return script
