# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Argument handling for IRI/PSI-J job submissions.

IRI/PSI-J execs `<executable> <args>` directly, as a NON-login process. That
bypasses a script's `#!/bin/bash -l` shebang, so `module`/`conda`/`mpiexec`
are off PATH and the job dies with exit 127. Every bash/sh invocation
therefore needs an explicit login flag.

The ALCF IRI API also caps `/bin/bash` at two arguments ("List of arguments
for /bin/bash must have a length of 2 at most") and documents the inline form
as `"arguments": ["-lc", "<commands>"]`. So the flag has to be folded into an
existing short-option group rather than prepended whenever one is present:
`-c cmd` becomes `-lc cmd`, never `-l -c cmd`.

That two-argument cap is also why the MCP `arguments` string cannot simply be
split on every comma. `bash -c` takes exactly ONE argument -- the command
string -- so a perfectly ordinary command like

    -lc,module load conda; mpiexec --cpu-bind list:0,1 ./a.out

must arrive as two arguments. Splitting on every comma yields three, which the
API rejects with the same HTTP 400, *and* truncates the command at the comma.
`parse_arguments` therefore keeps the command string verbatim, and accepts a
JSON array for callers that want to pass the list explicitly.
"""

from __future__ import annotations

import json
import os

_SHELLS = ("bash", "sh")


def _is_shell(executable: str) -> bool:
    return os.path.basename(executable) in _SHELLS


def _split_shell_command_form(raw: str) -> list[str] | None:
    """Parse the `-lc,<command>` form, keeping `<command>` verbatim.

    Returns None when `raw` is not leading-options-then-command, so the caller
    can fall back to the plain comma split.
    """
    opts: list[str] = []
    rest = raw
    while True:
        token, sep, remainder = rest.partition(",")
        token = token.strip()
        if len(token) < 2 or not token.startswith("-") or token == "--":
            return None
        opts.append(token)
        if token[1] != "-" and "c" in token[1:]:
            # Everything after this flag group is the single command string.
            # Do NOT split it, and do not strip interior commas.
            command = remainder.strip()
            return opts + ([command] if command else [])
        if not sep:
            return None
        rest = remainder


def parse_arguments(executable: str, arguments: str | None) -> list[str]:
    """Turn the MCP `arguments` string into the argv list PSI-J expects.

    Accepts either form:

    * a JSON array -- `["-lc", "echo a, b"]` -- which is unambiguous and is the
      recommended way to pass anything containing a comma; or
    * the legacy comma-separated string, where a leading shell flag group
      containing `c` ends the splitting and the rest is one command string.
    """
    if not arguments or not arguments.strip():
        return []

    raw = arguments.strip()

    if raw.startswith("["):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"arguments looks like JSON but does not parse: {exc}") from exc
        if not isinstance(parsed, list) or not all(isinstance(a, str) for a in parsed):
            raise ValueError("arguments JSON must be an array of strings")
        return parsed

    if _is_shell(executable):
        split = _split_shell_command_form(raw)
        if split is not None:
            return split

    return [a.strip() for a in raw.split(",") if a.strip()]


def _own_options(args: list[str]) -> list[tuple[int, str]]:
    """Return the (index, value) pairs the shell itself consumes as options.

    Scanning stops at the first operand -- a script path, `--`, or the command
    string after `-c` -- so flags belonging to the *script* are never mistaken
    for the shell's own. Without that, `bash script.sh -logfile x` looks like
    it already requested a login shell and the fix is silently skipped.
    """
    opts: list[tuple[int, str]] = []
    for i, arg in enumerate(args):
        if len(arg) < 2 or not arg.startswith("-") or arg == "--":
            break
        opts.append((i, arg))
        # A short group containing `c` takes the NEXT argument as the command
        # string to run, so nothing after this point is an option.
        if arg[1] != "-" and "c" in arg[1:]:
            break
    return opts


def ensure_login_shell(executable: str, args: list[str]) -> list[str]:
    """Return `args` guaranteed to start a login shell, without lengthening it.

    No-op when the executable is the script itself (basename is not a shell),
    because then the `#!/bin/bash -l` shebang is honored.
    """
    if not _is_shell(executable):
        return args

    opts = _own_options(args)

    for _, arg in opts:
        if arg == "--login" or (arg[1] != "-" and "l" in arg[1:]):
            return args  # already a login shell: -l, -lc, --login, ...

    for i, arg in opts:
        if arg[1] != "-":
            # Fold `l` into the first short group: `-c` -> `-lc`. Keeps the
            # argument count unchanged, which the ALCF API requires.
            return args[:i] + ["-l" + arg[1:]] + args[i + 1:]

    return ["-l"] + args
