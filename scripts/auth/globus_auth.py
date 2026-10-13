#!/usr/bin/env python3
# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""Unified Globus authentication for Transfer tokens.

Performs a single OAuth2 login flow to obtain a Globus Transfer token,
stores it in one file, and auto-refreshes when expired.  The ALCF IRI
token (different client ID) is handled by delegating to
alcf_iri_token.py via subprocess.

Usage:
    python scripts/auth/globus_auth.py authenticate           # interactive browser login
    python scripts/auth/globus_auth.py get_url                # print auth URL (non-interactive, saves PKCE state)
    python scripts/auth/globus_auth.py exchange --code <CODE> # exchange code after get_url
    python scripts/auth/globus_auth.py sync_env               # confirm tokens in .env and echo their keys
    python scripts/auth/globus_auth.py ensure_valid           # refresh if needed, then sync .env
    python scripts/auth/globus_auth.py status                 # show token expiry
    python scripts/auth/globus_auth.py logout                 # remove cached tokens

Add --facility alcf|nersc|olcf to authenticate/get_url to pick the right
identity-domain pin. Only OLCF needs one (ORNL SSO); ALCF and NERSC log in
unpinned, which is the default, so attendees at those two can omit the flag.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.parse

import globus_sdk

# ── Constants ────────────────────────────────────────────────────────

CLIENT_ID = "95fdeba8-fac2-42bd-a357-e068d82ff78e"  # Globus CLI public app

TRANSFER_ALL_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"

# One source of truth for Globus Transfer endpoint consent.
#
# Only request data_access for endpoint scopes we have verified Globus Auth
# accepts. Some real endpoint UUIDs in systems/*.yaml do not expose a
# corresponding Globus Auth data_access scope; requesting those causes
# UNKNOWN_SCOPE_ERROR before login. They stay in this registry for coverage but
# have data_access=False until verified.
TRANSFER_ENDPOINTS: dict[str, dict[str, object]] = {
    # ALCF shared endpoints
    "alcf_home": {
        "endpoint_id": "9032dd3a-e841-4687-a163-2720da731b5b",
        "facility": "ALCF",
        "data_access": True,
    },
    "alcf_eagle": {
        "endpoint_id": "05d2c76a-e867-4f67-aa57-76edeb0beda0",
        "facility": "ALCF",
        "data_access": True,
    },
    "alcf_flare": {
        "endpoint_id": "f39a7a0f-5bfc-46ce-9615-ba9f8592814f",
        "facility": "ALCF",
        "data_access": True,
    },
    "aurora_home": {
        "endpoint_id": "51f2b2c4-b4b8-11ee-8811-a52c65340a88",
        "facility": "ALCF",
        "data_access": False,
    },
    "sunspot_home": {
        "endpoint_id": "103c946c-3790-11f1-b05d-0afffe4617ab",
        "facility": "ALCF",
        "data_access": False,
    },
    "sirius_home": {
        "endpoint_id": "b2686037-3531-11f1-aa88-0ea3589134b3",
        "facility": "ALCF",
        "data_access": False,
    },
    # NERSC endpoints
    "nersc_dtn": {
        "endpoint_id": "9d6d994a-6d04-11e5-ba46-22000b92c6ec",
        "facility": "NERSC",
        "data_access": True,
    },
    "nersc_perlmutter": {
        "endpoint_id": "6bdc7956-fc0f-4ad2-989c-7aa5ee643a79",
        "facility": "NERSC",
        "data_access": True,
    },
    "nersc_hpss": {
        "endpoint_id": "9cd89cfd-6d04-11e5-ba46-22000b92c6ec",
        "facility": "NERSC",
        "data_access": False,
    },
    # OLCF endpoints
    "olcf_dtn": {
        "endpoint_id": "36d521b3-c182-4071-b7d5-91db5d380d42",
        "facility": "OLCF",
        "data_access": False,  # OLCF DTN does not expose data_access scope
        "session_domain": "sso.ccs.ornl.gov",
    },
}

# Identity domain to pin the login to, or None to let Globus show its normal
# identity chooser.
#
# This defaults to None ON PURPOSE. Globus Transfer's base scope works with ANY
# linked identity, so pinning a domain does not buy access -- it REMOVES it for
# everyone who lacks that identity. This used to default to sso.ccs.ornl.gov,
# which silently forced ALCF and NERSC users through ORNL's SSO and left them
# unable to mint a Transfer token at all.
#
# The ORNL value is still available opt-in via --session-domain, for the one
# case that needs it: landing on the OLCF DTN with an ORNL identity (see
# TRANSFER_ENDPOINTS["olcf_dtn"]["session_domain"]).
DEFAULT_SESSION_DOMAIN: str | None = None
OLCF_SESSION_DOMAIN = "sso.ccs.ornl.gov"


def known_facilities() -> list[str]:
    """Lowercase facility names that appear in TRANSFER_ENDPOINTS."""
    return sorted({str(e["facility"]).lower() for e in TRANSFER_ENDPOINTS.values()})


def facility_session_domain(facility: str) -> str | None:
    """Identity domain to pin ``facility``'s login to, or None to leave it open.

    Read out of TRANSFER_ENDPOINTS so the per-endpoint ``session_domain`` key is
    the single source of truth. Pinning a new facility then means adding that key
    to its entry, not editing a second map that can drift out of sync with it.

    Only OLCF carries a pin today; ALCF and NERSC return None ON PURPOSE, not for
    lack of a value. Their Transfer tokens work with any linked identity, so a pin
    would remove access rather than grant it (see DEFAULT_SESSION_DOMAIN). That
    makes ``--facility alcf`` a deliberate no-op: it exists so "which facility am
    I using?" is a question the CLI can answer directly, instead of making every
    user reason about identity domains only to discover theirs needs nothing.
    """
    want = facility.strip().upper()
    for entry in TRANSFER_ENDPOINTS.values():
        if str(entry["facility"]).upper() == want:
            return str(entry["session_domain"]) if entry.get("session_domain") else None
    raise ValueError(
        f"unknown facility {facility!r}; expected one of {', '.join(known_facilities())}"
    )


def _session_pin_kwargs(session_domain: str | None) -> dict[str, str]:
    """Authorize-URL kwargs that pin the login, or {} to leave it unpinned.

    These keys must be ABSENT, not None. globus_sdk builds the query string by
    filtering out its own MISSING sentinel -- it does not filter None -- so
    ``session_required_single_domain=None`` is urlencoded as the literal string
    "None" and Globus sees a domain named "None". Same for ``prompt``.
    """
    if not session_domain:
        return {}
    return {"session_required_single_domain": session_domain, "prompt": "login"}


def _build_transfer_data_access_scope(facility: str | None = None) -> str:
    """Transfer scope requesting data_access on verified collections.

    ``facility`` narrows the request to that facility's collections; None asks
    for all of them. Narrowing matters because data_access consent is granted
    per collection: an ALCF user who asks for everything is shown NERSC
    collections on the consent screen and has to approve access they have no
    business holding -- and every extra collection is another chance for the
    whole login to fail as UNKNOWN_SCOPE_ERROR.

    Collections with data_access=False are skipped either way (see the note on
    TRANSFER_ENDPOINTS); a facility whose collections are all unverified
    therefore falls back to the plain transfer scope.
    """
    want = facility.strip().upper() if facility else None
    data_access_scopes = [
        f"*https://auth.globus.org/scopes/{entry['endpoint_id']}/data_access"
        for entry in TRANSFER_ENDPOINTS.values()
        if entry.get("data_access")
        and (want is None or str(entry["facility"]).upper() == want)
    ]
    if not data_access_scopes:
        return TRANSFER_ALL_SCOPE
    return f"{TRANSFER_ALL_SCOPE}[{' '.join(data_access_scopes)}]"


# Keep the default browser login minimal and stable. Collection-specific
# data_access scopes are best requested reactively from Transfer's
# ConsentRequired.required_scopes response; guessing them upfront can turn the
# whole login into UNKNOWN_SCOPE_ERROR.
TRANSFER_SCOPE = TRANSFER_ALL_SCOPE
RESOURCE_SERVERS = {
    "transfer": "transfer.api.globus.org",
}

ENV_VAR_MAP = {
    "GLOBUS_TRANSFER_TOKEN": "transfer",
}

# Inverse: label -> env var name
_LABEL_TO_ENV = {v: k for k, v in ENV_VAR_MAP.items()}

PKCE_STATE_FILE = pathlib.Path("/tmp/globus_pkce_state.json")

# Project .env file
# Tutorial layout: this file lives at scripts/auth/globus_auth.py (one level
# deeper than the main repo's scripts/globus_auth.py), so the tutorial root is
# parent.parent.parent, not parent.parent.
ENV_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / ".env"

# Sibling token scripts live alongside this file in scripts/auth/ (not a
# further "auth" subdir — see the layout note above).
_AUTH_DIR = pathlib.Path(__file__).resolve().parent

# ALCF IRI token script (different client ID, separate OAuth2 flow)
_IRI_TOKEN_SCRIPT = _AUTH_DIR / "alcf_iri_token.py"

# NERSC IRI token script
_NERSC_IRI_TOKEN_SCRIPT = _AUTH_DIR / "nersc_iri_token.py"

# ALCF Inference Gateway token script
_INFERENCE_TOKEN_SCRIPT = _AUTH_DIR / "inference_auth_token.py"

# The env-var name holding each facility's IRI token. The DOE IRI hands-on
# session standardized on IRI_TOKEN_<FACILITY> and that is the only spelling
# written or read; the old <FACILITY>_IRI_TOKEN names were retired 2026-10-13.
# Mirrors IRI_TOKEN_NAMES in mcp/auth_env.py — keep the two in sync.
#
# Still tuples because _ensure_delegated_token and the status printer iterate
# them; a facility needing a second name again only has to add it here.
_ALCF_IRI_VARS = ("IRI_TOKEN_ALCF",)
_NERSC_IRI_VARS = ("IRI_TOKEN_NERSC",)
_OLCF_IRI_VARS = ("IRI_TOKEN_OLCF",)


# ── .env Helpers ─────────────────────────────────────────────────────


def _update_env(key: str, value: str) -> None:
    """Update or add a key=value pair in the .env file, with a timestamp comment.

    Writes a `#<KEY> updated: YYYY-MM-DD-HH-MM-SS` line directly above the
    `KEY=...` entry, replacing any prior timestamp comment for the same key.
    """
    timestamp = time.strftime("%Y-%m-%d-%H-%M-%S")
    timestamp_line = f"#{key} updated: {timestamp}"
    stamp_prefix = f"#{key} updated:"

    lines: list[str] = []
    found = False
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.lstrip()
            if stripped.startswith(stamp_prefix):
                # Drop any prior timestamp for this key; we'll re-add fresh.
                continue
            if stripped.startswith(f"export {key}=") or line.startswith(f"{key}="):
                lines.append(timestamp_line)
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(timestamp_line)
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _remove_env_keys(keys: list[str]) -> None:
    """Remove key=value lines (and their timestamp comments) from .env."""
    if not ENV_FILE.exists():
        return
    remove_prefixes = tuple(
        p
        for key in keys
        for p in (f"#{key} updated:", f"{key}=", f"export {key}=")
    )
    lines = [
        line for line in ENV_FILE.read_text().splitlines()
        if not any(line.lstrip().startswith(p) for p in remove_prefixes)
    ]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def _load_tokens() -> dict | None:
    """Load token data from .env.  Returns None if no tokens found."""
    if not ENV_FILE.exists():
        return None
    env_lines = ENV_FILE.read_text().splitlines()

    def _env_val(key: str) -> str:
        for line in env_lines:
            if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        return ""

    token_data: dict = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        env_var = _LABEL_TO_ENV.get(label, "")
        if not env_var:
            continue
        access_token = _env_val(env_var)
        if not access_token:
            continue
        refresh_token = _env_val(env_var + "_REFRESH")
        expires_at_str = _env_val(env_var + "_EXPIRES_AT")
        token_data[rs_key] = {
            "access_token": access_token,
            "refresh_token": refresh_token or None,
            "expires_at_seconds": float(expires_at_str) if expires_at_str else 0.0,
            "token_type": "Bearer",
        }
    return token_data if token_data else None


def _write_all_to_env(data: dict) -> None:
    """Write all tokens to .env.  *data* is keyed by label (transfer)."""
    for label, token_info in data.items():
        env_var = _LABEL_TO_ENV.get(label)
        if not env_var or "access_token" not in token_info:
            continue
        _update_env(env_var, token_info["access_token"])
        os.environ[env_var] = token_info["access_token"]
        if token_info.get("refresh_token"):
            _update_env(env_var + "_REFRESH", token_info["refresh_token"])
        if token_info.get("expires_at_seconds"):
            _update_env(env_var + "_EXPIRES_AT", str(int(token_info["expires_at_seconds"])))


# ── IRI Token Delegation ────────────────────────────────────────────


def _ensure_delegated_token(
    script: pathlib.Path,
    env_var: str | tuple[str, ...],
    *,
    interactive: bool = False,
    force: bool = False,
) -> bool:
    """Run a delegated token script, store the resulting token.

    - force=True: always run `authenticate --force-reauth` first (forces a
      fresh browser login), then capture the new token via get_access_token.
    - force=False, interactive=True: try get_access_token first; if it fails,
      fall back to interactive `authenticate` (which may reuse a cached token).
    - force=False, interactive=False: only attempt get_access_token (silent).

    ``env_var`` may be a tuple of names, in which case the token is written
    under every one of them. Every facility is down to a single name since the
    legacy spellings were retired (see mcp/auth_env.py), so the tuples are
    length 1 today; the loop stays because the cost is nil.
    """
    env_vars = (env_var,) if isinstance(env_var, str) else tuple(env_var)
    if not script.is_file():
        return True  # script not present, skip

    if force:
        try:
            # `--force-reauth` is supported by alcf_iri_token.py and
            # nersc_iri_token.py; inference_auth_token.py's `authenticate`
            # already forces a fresh login (and ignores the unknown flag).
            cmd = [sys.executable, str(script), "authenticate"]
            if script.name != "inference_auth_token.py":
                cmd.append("--force-reauth")
            result = subprocess.run(cmd, timeout=300)
            if result.returncode != 0:
                return False
        except Exception:
            return False

    try:
        result = subprocess.run(
            [sys.executable, str(script), "get_access_token"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            token = result.stdout.strip()
            for name in env_vars:
                _update_env(name, token)
                os.environ[name] = token
            return True
    except Exception:
        pass

    if interactive and not force:
        try:
            result = subprocess.run(
                [sys.executable, str(script), "authenticate"],
                timeout=300,
            )
            if result.returncode != 0:
                return False
        except Exception:
            return False
        # Try to capture the token after interactive login.
        try:
            result = subprocess.run(
                [sys.executable, str(script), "get_access_token"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                token = result.stdout.strip()
                for name in env_vars:
                    _update_env(name, token)
                    os.environ[name] = token
                return True
        except Exception:
            pass

    return False


def _ensure_iri_token(interactive: bool = False, force: bool = False) -> bool:
    """Ensure a valid ALCF IRI token, delegating to alcf_iri_token.py."""
    return _ensure_delegated_token(
        _IRI_TOKEN_SCRIPT, _ALCF_IRI_VARS,
        interactive=interactive, force=force,
    )


def _ensure_nersc_iri_token(interactive: bool = False, force: bool = False) -> bool:
    """Ensure a valid NERSC IRI token, delegating to nersc_iri_token.py."""
    return _ensure_delegated_token(
        _NERSC_IRI_TOKEN_SCRIPT, _NERSC_IRI_VARS,
        interactive=interactive, force=force,
    )


def _ensure_inference_token(interactive: bool = False, force: bool = False) -> bool:
    """Ensure a valid ALCF Inference Gateway token, delegating to inference_auth_token.py."""
    return _ensure_delegated_token(
        _INFERENCE_TOKEN_SCRIPT, "ALCF_INFERENCE_TOKEN",
        interactive=interactive, force=force,
    )


# Tokens that `authenticate` mints alongside the Globus Transfer token, each
# tagged with the facility it belongs to so --facility can skip the rest.
# Without the tag, `authenticate --facility alcf` walked an attendee through a
# NERSC login too -- three browser round-trips for a one-facility session.
#
# OLCF is absent on purpose: its IRI credential is a myOLCF PAT that the user
# pastes in by hand, so there is no interactive flow for us to drive.
_DELEGATED_TOKENS: tuple[tuple[str, str, Path, object], ...] = (
    ("ALCF IRI", "alcf", _IRI_TOKEN_SCRIPT, _ensure_iri_token),
    ("NERSC IRI", "nersc", _NERSC_IRI_TOKEN_SCRIPT, _ensure_nersc_iri_token),
    ("ALCF Inference", "alcf", _INFERENCE_TOKEN_SCRIPT, _ensure_inference_token),
)


def _delegated_tokens(facility: str | None) -> list[tuple[str, object]]:
    """(label, ensure_fn) pairs to mint for ``facility`` -- all of them if None.

    Entries whose helper script is missing are dropped here rather than inside
    the loop, so a "step N of M" count promises exactly as many logins as the
    attendee will actually be asked for.
    """
    want = facility.strip().lower() if facility else None
    return [
        (label, fn)
        for label, fac, script, fn in _DELEGATED_TOKENS
        if (want is None or fac == want) and script.is_file()
    ]


def _get_iri_expiry() -> str:
    """Get IRI token expiry as a human-readable string."""
    if not _IRI_TOKEN_SCRIPT.is_file():
        return ""

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_IRI_TOKEN_SCRIPT),
                "get_time_until_token_expiration",
                "--units",
                "hours",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"{result.stdout.strip()}h remaining"
    except Exception:
        pass
    return ""


def _get_nersc_iri_expiry() -> str:
    """Get NERSC IRI token expiry as a human-readable string."""
    if not _NERSC_IRI_TOKEN_SCRIPT.is_file():
        return ""

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_NERSC_IRI_TOKEN_SCRIPT),
                "get_time_until_token_expiration",
                "--units",
                "hours",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"{result.stdout.strip()}h remaining"
    except Exception:
        pass
    return ""


def _get_inference_expiry() -> str:
    """Get ALCF Inference Gateway token expiry as a human-readable string."""
    if not _INFERENCE_TOKEN_SCRIPT.is_file():
        return ""

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_INFERENCE_TOKEN_SCRIPT),
                "get_time_until_token_expiration",
                "--units",
                "hours",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return f"{result.stdout.strip()}h remaining"
    except Exception:
        pass
    return ""


def _get_jwt_env_expiry(env_var: str | tuple[str, ...]) -> str:
    """Return human-readable expiry for a JWT stored in .env under *env_var*.

    Used for myOLCF-issued Project Access Tokens (IRI_TOKEN_OLCF,
    OLCF_S3M_TOKEN) which live only in .env — there is no helper script that can
    refresh them. PATs (type=opat) typically have no `exp` claim — they live
    until revoked — so we report "no expiry" rather than treating
    absence-of-exp as expired.

    *env_var* may be a tuple of accepted spellings, in which case the first one
    present in .env wins — matching the read order the clients use.
    """
    if not ENV_FILE.exists():
        return ""
    env_vars = (env_var,) if isinstance(env_var, str) else tuple(env_var)
    found: dict[str, str] = {}
    for line in ENV_FILE.read_text().splitlines():
        for name in env_vars:
            if name in found or not line.startswith(f"{name}="):
                continue
            value = line.split("=", 1)[1].strip().strip('"').strip("'")
            # An unexpanded ${VAR} placeholder is as good as absent.
            if value and not value.startswith("${"):
                found[name] = value
    token = next((found[name] for name in env_vars if name in found), "")
    if not token or token.count(".") != 2:
        return ""
    import base64
    try:
        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)  # JWT base64url padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return ""
    exp = payload.get("exp")
    if exp is None:
        return "no expiry (PAT)"
    now = time.time()
    if float(exp) > now + 60:
        return f"{(float(exp) - now) / 3600:.1f}h remaining"
    return ""


def _print_all_tokens() -> None:
    """Print all tokens we manage (Transfer, ALCF IRI, NERSC IRI, Inference).

    Reads the Globus tokens from disk and the delegated tokens by invoking
    each helper script's `get_access_token` action.  Prints each on its own
    line so the user can copy or pipe individual values.
    """
    print("\n=== Tokens ===")

    # Globus Transfer (loaded from .env).
    token_data = _load_tokens() or {}
    for label, rs_key in RESOURCE_SERVERS.items():
        token = token_data.get(rs_key, {}).get("access_token", "")
        env_var = _LABEL_TO_ENV.get(label, label.upper())
        print(f"\n{env_var}:")
        print(token if token else "(not available)")

    # Delegated tokens, each under its single canonical name (see
    # _ALCF_IRI_VARS).
    for env_var, script in (
        (_ALCF_IRI_VARS[0], _IRI_TOKEN_SCRIPT),
        (_NERSC_IRI_VARS[0], _NERSC_IRI_TOKEN_SCRIPT),
        ("ALCF_INFERENCE_TOKEN", _INFERENCE_TOKEN_SCRIPT),
    ):
        token = ""
        if script.is_file():
            try:
                result = subprocess.run(
                    [sys.executable, str(script), "get_access_token"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    token = result.stdout.strip()
            except Exception:
                pass
        print(f"\n{env_var}:")
        print(token if token else "(not available)")


# ── Core Logic ───────────────────────────────────────────────────────


def _extract_auth_code(value: str) -> str:
    """Accept either a raw auth code or a URL/query string containing code=."""
    text = value.strip()
    if not text:
        return text

    parsed = urllib.parse.urlparse(text)
    query = parsed.query or text
    params = urllib.parse.parse_qs(query)
    code_values = params.get("code")
    if code_values:
        return code_values[0].strip()
    return text


def _print_auth_api_error(exc: Exception) -> None:
    """Print the useful Globus Auth error body without a traceback."""
    print("Globus Auth token exchange failed.")
    message = getattr(exc, "message", None)
    code = getattr(exc, "code", None)
    raw_json = getattr(exc, "raw_json", None)
    raw_text = getattr(exc, "raw_text", None)
    if message:
        print(f"  message: {message}")
    if code:
        print(f"  code: {code}")
    if raw_json:
        print(f"  response: {json.dumps(raw_json, indent=2)}")
    elif raw_text:
        print(f"  response: {raw_text}")
    else:
        print(f"  error: {exc}")
    print(
        "Retry with a fresh URL/code. Authorization codes are single-use and "
        "expire quickly; paste only the code or the full redirect URL containing code=."
    )


def authenticate(
    *,
    include_data_access: bool = False,
    session_domain: str | None = DEFAULT_SESSION_DOMAIN,
    facility: str | None = None,
) -> dict | None:
    """Run browser-based OAuth2 login for Globus Transfer.

    ``session_domain`` pins the login to one identity provider. Leave it None
    (the default) to get Globus's normal identity chooser -- that is what any
    non-OLCF user wants. It was previously hard-wired to ORNL here and ignored
    the --session-domain flag, so there was no way to opt out.

    ``facility`` narrows two things: which collections' data_access scopes are
    requested (only under ``include_data_access`` -- the base Transfer scope is
    not collection-specific, so there is nothing to narrow), and which
    delegated facility tokens get minted afterwards. An ALCF attendee should
    never be walked through a NERSC login.
    """
    client = globus_sdk.NativeAppAuthClient(CLIENT_ID)
    scopes = (
        _build_transfer_data_access_scope(facility)
        if include_data_access
        else TRANSFER_SCOPE
    )
    client.oauth2_start_flow(requested_scopes=scopes, refresh_tokens=True)

    authorize_url = client.oauth2_get_authorize_url(**_session_pin_kwargs(session_domain))

    # Count the whole sequence up front and number each leg. Several separate
    # browser logins with no labels read as one flow looping on itself.
    pending = _delegated_tokens(facility)
    total = 1 + len(pending)
    print(f"\n=== Login 1 of {total}: Globus Transfer (file staging) ===")
    print(f"Open this URL in your browser:\n\n  {authorize_url}\n")
    auth_code = _extract_auth_code(input("Paste the authorization code here: "))

    try:
        token_response = client.oauth2_exchange_code_for_tokens(auth_code)
    except Exception as exc:
        _print_auth_api_error(exc)
        return None
    by_rs = token_response.by_resource_server

    token_data: dict = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        rs_data = by_rs.get(rs_key)
        if rs_data:
            token_data[rs_key] = {
                "access_token": rs_data["access_token"],
                "refresh_token": rs_data.get("refresh_token"),
                "expires_at_seconds": rs_data.get("expires_at_seconds", 0),
                "token_type": rs_data.get("token_type", "Bearer"),
            }

    if not token_data:
        print("Error: No tokens received.")
        print("Available resource servers:", list(by_rs.keys()))
        return None

    label_data = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        if rs_key in token_data:
            label_data[label] = token_data[rs_key]
    _write_all_to_env(label_data)

    print(f"Globus Transfer token saved to {ENV_FILE}")

    # Force a fresh interactive login for every other token we manage so
    # `authenticate` always returns a complete, freshly-issued token set --
    # narrowed to --facility, if given, by _delegated_tokens above.
    delegated_ok = True
    for step, (label, ensure_fn) in enumerate(pending, start=2):
        print(f"\n=== Login {step} of {total}: {label} ===")
        if not ensure_fn(interactive=True, force=True):
            print(f"Error: {label} token authentication failed.")
            delegated_ok = False

    if not delegated_ok:
        print(
            "The Transfer token was saved, but one or more secondary "
            "tokens failed. Run `python scripts/auth/globus_auth.py status` to see "
            "what is still missing."
        )
        return None

    status()
    return token_data


def ensure_valid() -> bool:
    """Check all tokens, refresh if needed, write to .env.  Returns True if valid."""
    token_data = _load_tokens()
    if not token_data:
        return False

    # Check if any token is expired
    now = time.time()
    any_expired = False
    for rs_key in RESOURCE_SERVERS.values():
        rs_data = token_data.get(rs_key, {})
        expires_at = rs_data.get("expires_at_seconds", 0)
        if expires_at <= now + 60:
            any_expired = True
            break

    if any_expired:
        # Find a refresh token
        refresh_token = None
        for rs_key in RESOURCE_SERVERS.values():
            rs_data = token_data.get(rs_key, {})
            rt = rs_data.get("refresh_token")
            if rt:
                refresh_token = rt
                break

        if not refresh_token:
            return False

        try:
            client = globus_sdk.NativeAppAuthClient(CLIENT_ID)
            response = client.oauth2_refresh_token(refresh_token)
            by_rs = response.by_resource_server

            for rs_key in RESOURCE_SERVERS.values():
                refreshed = by_rs.get(rs_key)
                if refreshed:
                    token_data[rs_key] = {
                        "access_token": refreshed["access_token"],
                        "refresh_token": refreshed.get("refresh_token"),
                        "expires_at_seconds": refreshed.get("expires_at_seconds", 0),
                        "token_type": refreshed.get("token_type", "Bearer"),
                    }

        except Exception as exc:
            print(f"Token refresh failed: {exc}")
            return False

    # Write to .env and env vars
    label_data = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        if rs_key in token_data:
            label_data[label] = token_data[rs_key]
    _write_all_to_env(label_data)

    # Handle IRI + Inference tokens (best-effort; does not affect return value)
    _ensure_iri_token(interactive=False)
    _ensure_nersc_iri_token(interactive=False)
    _ensure_inference_token(interactive=False)

    return True


def status() -> None:
    """Print token status for all services."""
    token_data = _load_tokens()
    now = time.time()

    print("=== Globus Token Status ===\n")

    if not token_data:
        print(f"No Globus tokens found in {ENV_FILE}. Run: python scripts/auth/globus_auth.py authenticate")
    else:
        for label, rs_key in RESOURCE_SERVERS.items():
            rs_data = token_data.get(rs_key, {})
            expires_at = rs_data.get("expires_at_seconds", 0)
            if not rs_data.get("access_token"):
                print(f"  {label:10s}: NOT FOUND")
            elif expires_at <= now + 60:
                print(f"  {label:10s}: EXPIRED")
            else:
                remaining = (expires_at - now) / 3600
                print(f"  {label:10s}: {remaining:.1f}h remaining")

    # ALCF IRI token
    iri_expiry = _get_iri_expiry()
    if iri_expiry:
        print(f"  {'alcf_iri':10s}: {iri_expiry}")
    elif _IRI_TOKEN_SCRIPT.is_file():
        print(f"  {'alcf_iri':10s}: EXPIRED or NOT FOUND")
    else:
        print(f"  {'alcf_iri':10s}: (no script)")

    # NERSC IRI token
    nersc_expiry = _get_nersc_iri_expiry()
    if nersc_expiry:
        print(f"  {'nersc_iri':10s}: {nersc_expiry}")
    elif _NERSC_IRI_TOKEN_SCRIPT.is_file():
        print(f"  {'nersc_iri':10s}: EXPIRED or NOT FOUND")
    else:
        print(f"  {'nersc_iri':10s}: (no script)")

    # OLCF IRI / S3M tokens (manual JWT PATs, refreshed via myOLCF web UI only)
    for label, env_var in (("olcf_iri", _OLCF_IRI_VARS), ("olcf_s3m", "OLCF_S3M_TOKEN")):
        expiry = _get_jwt_env_expiry(env_var)
        if expiry:
            print(f"  {label:10s}: {expiry}")
        else:
            print(f"  {label:10s}: EXPIRED or NOT FOUND (manual refresh: myOLCF)")

    # ALCF Inference Gateway token
    inference_expiry = _get_inference_expiry()
    if inference_expiry:
        print(f"  {'inference':10s}: {inference_expiry}")
    elif _INFERENCE_TOKEN_SCRIPT.is_file():
        print(f"  {'inference':10s}: EXPIRED or NOT FOUND")
    else:
        print(f"  {'inference':10s}: (no script)")


def logout() -> None:
    """Remove Globus Transfer tokens from .env."""
    keys: list[str] = []
    for env_var in ENV_VAR_MAP:
        keys += [env_var, env_var + "_REFRESH", env_var + "_EXPIRES_AT"]
    _remove_env_keys(keys)
    print(f"Globus Transfer tokens removed from {ENV_FILE}")


def get_url(
    session_domain: str | None = DEFAULT_SESSION_DOMAIN,
    *,
    include_data_access: bool = False,
    facility: str | None = None,
) -> str | None:
    """Print the auth URL and save PKCE state for later exchange (non-interactive).

    ``facility`` narrows the requested data_access scopes exactly as it does in
    authenticate(); it is inert without ``include_data_access``.
    """
    client = globus_sdk.NativeAppAuthClient(CLIENT_ID)
    scopes = (
        _build_transfer_data_access_scope(facility)
        if include_data_access
        else TRANSFER_SCOPE
    )
    client.oauth2_start_flow(requested_scopes=scopes, refresh_tokens=True)

    verifier = getattr(client.current_oauth2_flow_manager, "verifier", None)
    if not verifier:
        print("Error: could not retrieve PKCE verifier from flow manager.")
        return None

    PKCE_STATE_FILE.write_text(json.dumps({"client_id": CLIENT_ID, "verifier": verifier}))

    url = client.oauth2_get_authorize_url(**_session_pin_kwargs(session_domain))
    print(url)
    return url


def exchange(auth_code: str) -> dict | None:
    """Exchange an auth code using a previously saved PKCE state."""
    if not PKCE_STATE_FILE.exists():
        print(f"No PKCE state found at {PKCE_STATE_FILE}. Run get_url first.")
        return None

    auth_code = _extract_auth_code(auth_code)
    state = json.loads(PKCE_STATE_FILE.read_text())
    verifier = state["verifier"]

    import urllib.parse as _uparse
    import urllib.request as _urequest

    data = _uparse.urlencode({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": "https://auth.globus.org/v2/web/auth-code",
        "client_id": CLIENT_ID,
        "code_verifier": verifier,
    }).encode()

    req = _urequest.Request(
        "https://auth.globus.org/v2/oauth2/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with _urequest.urlopen(req) as r:
            resp = json.loads(r.read())
    except Exception as e:
        body = ""
        if hasattr(e, "read"):
            try:
                body = e.read().decode()
            except Exception:
                body = ""
        print(f"Token exchange failed: {body or e}")
        return None

    def _entry(d: dict) -> dict:
        expires_at = d.get("expires_at_seconds") or (
            int(time.time()) + d.get("expires_in", 0)
        )
        return {
            "access_token": d["access_token"],
            "refresh_token": d.get("refresh_token"),
            "expires_at_seconds": expires_at,
            "token_type": d.get("token_type", "Bearer"),
        }

    token_data: dict = {}
    resource_server = resp.get("resource_server", "transfer.api.globus.org")
    token_data[resource_server] = _entry(resp)
    for other in resp.get("other_tokens", []):
        rs = other.get("resource_server", "unknown")
        token_data[rs] = _entry(other)

    label_data = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        if rs_key in token_data:
            label_data[label] = token_data[rs_key]
    _write_all_to_env(label_data)

    PKCE_STATE_FILE.unlink(missing_ok=True)

    print(f"Tokens saved to {ENV_FILE}")
    for label, rs_key in RESOURCE_SERVERS.items():
        if rs_key in token_data:
            t = token_data[rs_key]["access_token"]
            print(f"  {label}: {t[:20]}...")

    _ensure_iri_token(interactive=False)
    _ensure_nersc_iri_token(interactive=False)
    _ensure_inference_token(interactive=False)
    return token_data


def sync_env() -> bool:
    """Confirm current tokens in .env are present and echo their keys."""
    token_data = _load_tokens()
    if not token_data:
        print(f"No Globus tokens found in {ENV_FILE}. Run: python scripts/auth/globus_auth.py authenticate")
        return False

    label_data = {}
    for label, rs_key in RESOURCE_SERVERS.items():
        if rs_key in token_data:
            label_data[label] = token_data[rs_key]

    if not label_data:
        print("No matching tokens found in token file.")
        print("Available resource servers:", list(token_data.keys()))
        return False

    _write_all_to_env(label_data)
    print(f"Tokens confirmed in {ENV_FILE}:")
    for label, info in label_data.items():
        env_var = _LABEL_TO_ENV.get(label, label)
        token_preview = info["access_token"][:20] + "..."
        print(f"  {env_var} = {token_preview}")

    _ensure_iri_token(interactive=False)
    _ensure_nersc_iri_token(interactive=False)
    _ensure_inference_token(interactive=False)
    return True


# ── CLI ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Unified Globus token management")
    parser.add_argument(
        "action",
        choices=["authenticate", "get_url", "exchange", "ensure_valid", "sync_env", "status", "logout"],
    )
    parser.add_argument("--code", help="Auth code for the exchange action")
    # Mutually exclusive so there is no precedence rule to remember: argparse
    # rejects both-at-once loudly instead of silently picking a winner.
    pin = parser.add_mutually_exclusive_group()
    pin.add_argument(
        "--facility",
        choices=known_facilities(),
        help=(
            "Facility you are logging in for. Does three things: picks the "
            "right identity-domain pin ('olcf' pins the login to ORNL SSO; "
            "'alcf' and 'nersc' stay unpinned, which is what those need); "
            "limits --include-data-access to that facility's collections so "
            "you are not asked to consent to other facilities' storage; and "
            "skips the other facilities' token logins, so `authenticate "
            "--facility alcf` never sends you through a NERSC login."
        ),
    )
    pin.add_argument(
        "--session-domain",
        help=(
            "Pin the login to one identity domain by name -- the escape hatch "
            "for a domain --facility does not cover. Default: no pin, so Globus "
            "shows its normal identity chooser, which is correct for ALCF and "
            f"NERSC. Equivalent to --facility olcf: {OLCF_SESSION_DOMAIN!r}."
        ),
    )
    parser.add_argument(
        "--include-data-access",
        action="store_true",
        help=(
            "Also request verified collection data_access dependent scopes. "
            "Default auth deliberately omits these to avoid UNKNOWN_SCOPE_ERROR."
        ),
    )
    args = parser.parse_args()

    # One resolver for both flows, so `authenticate` and `get_url` can never
    # disagree about which domain the login is pinned to.
    if args.facility:
        session_domain = facility_session_domain(args.facility)
    elif args.session_domain is None:
        session_domain = DEFAULT_SESSION_DOMAIN
    else:
        session_domain = args.session_domain

    if args.action == "authenticate":
        ok = authenticate(
            include_data_access=args.include_data_access,
            session_domain=session_domain,
            facility=args.facility,
        )
        sys.exit(0 if ok else 1)
    elif args.action == "get_url":
        get_url(
            session_domain=session_domain,
            include_data_access=args.include_data_access,
            facility=args.facility,
        )
    elif args.action == "exchange":
        if not args.code:
            print("Error: --code is required for exchange action.")
            sys.exit(1)
        ok = exchange(args.code)
        sys.exit(0 if ok else 1)
    elif args.action == "ensure_valid":
        ok = ensure_valid()
        if not ok:
            # ensure_valid() can only REFRESH; it has no way to mint a first
            # token, because the initial consent is a browser flow. It used to
            # just exit 1 in silence, which reads as "Globus is broken" rather
            # than "you have not logged in yet" -- so spell out the bootstrap.
            print(
                "No usable Globus token.\n"
                "\n"
                "`ensure_valid` only REFRESHES an existing token. The first login\n"
                "is a browser consent flow, so bootstrap it once:\n"
                "\n"
                "    python scripts/auth/globus_auth.py authenticate\n"
                "        # prints a URL -> open it, log in, copy the auth code\n"
                "        # on OLCF, add: --facility olcf\n"
                "    python scripts/auth/globus_auth.py exchange --code <CODE>\n"
                "\n"
                "Then `ensure_valid` keeps it fresh from there on.",
                file=sys.stderr,
            )
        sys.exit(0 if ok else 1)
    elif args.action == "sync_env":
        ok = sync_env()
        sys.exit(0 if ok else 1)
    elif args.action == "status":
        status()
    elif args.action == "logout":
        logout()


if __name__ == "__main__":
    main()
