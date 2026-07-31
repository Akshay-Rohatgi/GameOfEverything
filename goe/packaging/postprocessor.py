"""Post-processing pipeline for the final concatenated deployment script.

Ported verbatim from v1 ``src/game_of_everything/script_postprocessor.py`` —
these functions are pure (no GoEState dependency) so they live in the v2 tree
to keep ``goe/`` independent of the v1 package.

Each processor is a plain function: (str) -> str.
"""

from __future__ import annotations

import re
from typing import Callable


def inject_shebang(script: str) -> str:
    """Remove any existing shebang lines and prepend exactly one #!/bin/bash."""
    lines = script.splitlines()
    cleaned = [line for line in lines if not line.startswith("#!")]
    return "#!/bin/bash\n" + "\n".join(cleaned)


def ensure_set_e(script: str) -> str:
    """Ensure 'set -e' appears immediately after the shebang line."""
    lines = script.splitlines()
    if not lines:
        return script

    insert_at = 1 if lines[0].startswith("#!") else 0
    cleaned = [
        line for line in lines
        if line.strip() not in ("set -e", "set -o errexit")
    ]
    cleaned.insert(insert_at, "set -e")
    return "\n".join(cleaned)


def normalize_blank_lines(script: str) -> str:
    """Collapse runs of more than two consecutive blank lines into two."""
    return re.sub(r"\n{3,}", "\n\n", script)


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
