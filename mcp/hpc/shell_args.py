# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Login-shell normalization for IRI/PSI-J job submissions.

IRI/PSI-J execs `<executable> <args>` directly, as a NON-login process. That
bypasses a script's `#!/bin/bash -l` shebang, so `module`/`conda`/`mpiexec`
are off PATH and the job dies with exit 127. Every bash/sh invocation
therefore needs an explicit login flag.

The ALCF IRI API also caps `/bin/bash` at two arguments ("List of arguments
for /bin/bash must have a length of 2 at most") and documents the inline form
as `"arguments": ["-lc", "<commands>"]`. So the flag has to be folded into an
existing short-option group rather than prepended whenever one is present:
`-c cmd` becomes `-lc cmd`, never `-l -c cmd`.
"""

from __future__ import annotations

import os

_SHELLS = ("bash", "sh")


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
    if os.path.basename(executable) not in _SHELLS:
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
