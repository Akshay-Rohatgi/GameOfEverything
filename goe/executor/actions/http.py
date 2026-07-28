"""HTTP action handler — executes curl from the attacker container."""

from __future__ import annotations
import re
import shlex
from typing import TYPE_CHECKING
from goe.executor.actions import ActionResult

if TYPE_CHECKING:
    from goe.container.environment import TestEnvironment


def http_request(
    env: "TestEnvironment",
    method: str,
    url: str,
    headers: dict[str, str],
    body: str | None,
) -> ActionResult:
    """Execute an HTTP request via curl in the attacker container."""
    parts = ["curl", "-s", "-i", "-X", method.upper()]
    for k, v in headers.items():
        parts += ["-H", f"{k}: {v}"]
    if body is not None:
        parts += ["--data-binary", body]
    parts.append(url)

    cmd = " ".join(shlex.quote(p) for p in parts)
    exit_code, stdout, stderr = env.exec_in("attacker", cmd)

    # Parse HTTP response: headers section + body separated by blank line
    status_code = None
    response_headers: dict[str, str] = {}
    response_body = ""

    if stdout:
        # Split on first blank line (CRLF or LF)
        header_section, _, response_body = stdout.partition("\r\n\r\n")
        if not _:
            header_section, _, response_body = stdout.partition("\n\n")

        lines = header_section.splitlines()
        if lines:
            # e.g. "HTTP/1.1 200 OK"
            m = re.match(r"HTTP/[\d.]+ (\d+)", lines[0])
            if m:
                status_code = int(m.group(1))
        for line in lines[1:]:
            if ":" in line:
                k, _, v = line.partition(":")
                response_headers[k.strip().lower()] = v.strip()

    return ActionResult(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        status_code=status_code,
        body=response_body,
        headers=response_headers,
    )
