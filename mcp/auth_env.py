# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Centralized per-user .env resolution for every MCP server in this repo.

WHY THIS EXISTS
---------------
Claude Code spawns project-local (repo-relative) MCP stdio servers with their
``CLAUDE_*`` environment variables STRIPPED. So the per-user ``CLAUDE_ENV_FILE``
that Trinity sets on the agent process never reaches the MCP servers — every
server would fall back to the shared repo-root ``.env`` and read/write one
tenant's tokens into the file all tenants load (a cross-tenant credential leak).

Non-``CLAUDE_`` variables DO survive that hop, so Trinity exports
``TRINITY_ENV_DIR`` (the directory that holds this session's private ``.env``),
and every server resolves ``$TRINITY_ENV_DIR/.env`` through this one module.

Resolution order (first hit wins):
  1. ``$TRINITY_ENV_DIR/.env``   — per-user, set by Trinity (the normal path)
  2. ``$CLAUDE_ENV_FILE``        — non-stripped contexts (e.g. ``--mcp-config``)
  3. ``<repo-root>/.env``        — standalone CLI use outside Trinity
"""
from __future__ import annotations

import os
from pathlib import Path

# Repo root = parent of mcp/ (this file lives at mcp/auth_env.py).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def env_file() -> Path:
    """Absolute path to this session's credentials .env (see module docstring)."""
    d = os.environ.get("TRINITY_ENV_DIR", "").strip()
    if d:
        return Path(d) / ".env"
    f = os.environ.get("CLAUDE_ENV_FILE", "").strip()
    if f:
        return Path(f)
    return _REPO_ROOT / ".env"


# IRI token env-var aliases, in resolution order (first non-empty wins).
#
# The DOE IRI hands-on session (doe-iri/iri-facility-api-examples) standardized
# on ``IRI_TOKEN_<FACILITY>``; this repo predates that and used
# ``<FACILITY>_IRI_TOKEN``. Attendees reach our segment with the IRI session's
# variables already exported, so that spelling is preferred — but the older
# names keep working so existing .env files do not break.
#
# ESnet is intentionally absent: it authenticates with an AmSC PAT
# (``AMSC_TOKEN``), not a facility IRI token.
IRI_TOKEN_ALIASES: dict[str, tuple[str, ...]] = {
    "alcf": ("IRI_TOKEN_ALCF", "ALCF_IRI_TOKEN", "ALCF_IRI_ACCESS_TOKEN"),
    "nersc": ("IRI_TOKEN_NERSC", "NERSC_IRI_TOKEN", "NERSC_IRI_ACCESS_TOKEN"),
    "olcf": ("IRI_TOKEN_OLCF", "OLCF_IRI_TOKEN"),
}


def token_names(facility: str) -> tuple[str, ...]:
    """Accepted env-var spellings for ``facility``, most-preferred first."""
    try:
        return IRI_TOKEN_ALIASES[facility.lower()]
    except KeyError:
        raise ValueError(
            f"unknown IRI facility {facility!r}; "
            f"expected one of {', '.join(sorted(IRI_TOKEN_ALIASES))}"
        ) from None


def write_token_names(facility: str) -> tuple[str, ...]:
    """Env-var names an auth flow should WRITE for ``facility``.

    Both supported spellings are written together so they can never drift apart
    — otherwise a re-auth would refresh one name while a stale value lingered
    under the other, and the preferred alias would silently win.

    The trailing ``*_IRI_ACCESS_TOKEN`` alias is read-only legacy and is not
    written back.
    """
    return token_names(facility)[:2]


def _usable(value: str) -> str:
    """A value that is empty or an unexpanded ``${VAR}`` placeholder is absent.

    Writing ``OLCF_IRI_TOKEN=${OLCF_IRI_TOKEN}`` into .env is a common setup
    slip. Treating it as set would mask a perfectly good value under one of the
    other aliases, so skip it and keep looking.
    """
    value = (value or "").strip()
    return "" if value.startswith("${") else value


def read_token(facility: str) -> str:
    """Freshest IRI token for ``facility`` read straight from env_file().

    Use this in MCP servers (see read_value for why os.environ is not enough).
    """
    for name in token_names(facility):
        value = _usable(read_value(name))
        if value:
            return value
    return ""


def environ_token(facility: str) -> str:
    """IRI token for ``facility`` from os.environ.

    Use this in API clients, which are also usable as plain libraries outside an
    MCP server and so must honour a token exported in the shell.
    """
    for name in token_names(facility):
        value = _usable(os.environ.get(name, ""))
        if value:
            return value
    return ""


def local_globus_endpoint() -> str:
    """UUID of *this machine's* Globus Connect Personal collection.

    This is the laptop/workstation endpoint that staging uses as the transfer
    SOURCE. It is identical for every facility (ALCF / NERSC / OLCF) because it
    identifies this machine, not the destination.

    Discovery order (first hit wins):
      1. ``LOCAL_GLOBUS_ENDPOINT`` — an explicit override, read from os.environ
         first and then from the per-user .env, so an attendee can pin it.
      2. ``globus_sdk.LocalGlobusConnectPersonal().endpoint_id`` — reads
         ``~/.globusonline/lta/client-id.txt``, exactly as
         ``globus endpoint local-id`` does. This is the autodetect path.

    Raises ``RuntimeError`` with setup guidance if neither yields an endpoint,
    which is also the honest signal that Globus Connect Personal is not set up
    on this machine — no transfer would work until it is.
    """
    override = _usable(os.environ.get("LOCAL_GLOBUS_ENDPOINT", "")) or _usable(
        read_value("LOCAL_GLOBUS_ENDPOINT")
    )
    if override:
        return override
    try:
        from globus_sdk import LocalGlobusConnectPersonal

        ep = LocalGlobusConnectPersonal().endpoint_id
    except Exception:
        ep = None
    if not ep:
        raise RuntimeError(
            "No local Globus Connect Personal collection found on this machine. "
            "Install and start Globus Connect Personal (see the GCP setup lab), "
            "or set LOCAL_GLOBUS_ENDPOINT in your .env to pin it explicitly."
        )
    return ep


def read_value(key: str) -> str:
    """Current value of ``key`` read STRAIGHT from env_file(), bypassing
    os.environ.

    MCP servers are long-lived and their ``_load_env`` deliberately does not
    overwrite an already-set os.environ var — so a token rotated externally
    (login/refresh) never reaches a call that reads os.environ. Credential reads
    must go through here so they always see the freshest value on disk.
    """
    f = env_file()
    if not f.exists():
        return ""
    for line in f.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        k, _, v = line.partition("=")
        if k.strip() == key:
            v = v.strip()
            # tolerate single/double quoted values (see user_tokens._env_quote)
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
                v = v[1:-1]
            return v
    return ""
