#!/usr/bin/env python3
# Author: Huihuo Zheng, huihuo.zheng@anl.gov
# Copyright: Trinity Science 2026

"""
Browser-based Globus authentication helper for the ALCF IRI API examples.

This script:
- launches a local browser login flow
- stores refresh/access tokens on disk
- refreshes tokens when needed
- can open a local token page with a copy button
"""

from __future__ import annotations

import argparse
import html
import json
import os
import pathlib
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from string import Template

import globus_sdk
from globus_sdk.login_flows import LocalServerLoginFlowManager

try:
    # Manual copy/paste OAuth flow — no browser, no localhost callback.
    # Correct for headless / remote (SSH) sessions.
    from globus_sdk.login_flows import CommandLineLoginFlowManager
except ImportError:  # older globus_sdk
    CommandLineLoginFlowManager = None

try:
    from globus_sdk import GlobusAuthorizationParameters
except ImportError:
    # globus-sdk 3.x exposes this in globus_sdk.gare
    from globus_sdk.gare import GlobusAuthorizationParameters


APP_NAME = "alcf_facility_api_app"
# NERSC AmSC native app client (works for ALCF IRI since access was relaxed 2026-05)
AUTH_CLIENT_ID = "fae5c579-490a-4d76-b6eb-d78f65caeb63"
SCOPE_CLIENT_ID = "6be511f6-a071-471f-9bc0-02a0d0836723"
SCOPE_STRING = f"https://auth.globus.org/scopes/{SCOPE_CLIENT_ID}/filesystem"
AUTH_SCOPES = ["openid", "profile", "email"]
# Project .env file
def _resolve_env_file() -> pathlib.Path:
    """Credentials .env: $TRINITY_ENV_DIR/.env → $CLAUDE_ENV_FILE → repo-root/.env.
    Mirrors mcp/auth_env.py so refreshed tokens land where the MCP servers read
    them (bug fix: this script lives in scripts/auth/, so repo root is
    parent.parent.PARENT, not parent.parent → scripts/.env)."""
    import os
    d = os.environ.get("TRINITY_ENV_DIR", "").strip()
    if d:
        return pathlib.Path(d) / ".env"
    f = os.environ.get("CLAUDE_ENV_FILE", "").strip()
    if f:
        return pathlib.Path(f)
    return pathlib.Path(__file__).resolve().parent.parent.parent / ".env"

ENV_FILE = _resolve_env_file()
# The DOE IRI hands-on session standardized on IRI_TOKEN_<FACILITY>, and that
# is now the only spelling this repo writes or reads. Mirrors IRI_TOKEN_NAMES
# in mcp/auth_env.py.
ENV_VAR = "IRI_TOKEN_ALCF"
ENV_VARS = (ENV_VAR,)
# Retired 2026-10-13. Never written, never read -- listed only so logout and
# re-auth can clear a stale value left behind in an older .env, which would
# otherwise sit there forever looking like a live credential.
RETIRED_ENV_VARS = ("ALCF_IRI_TOKEN", "ALCF_IRI_ACCESS_TOKEN")
REFRESH_VAR = "ALCF_IRI_REFRESH_TOKEN"
EXPIRES_VAR = "ALCF_IRI_EXPIRES_AT"


def _update_env(key: str, value: str) -> None:
    """Update or add a key=value pair in .env, with a timestamp comment above it."""
    timestamp = time.strftime("%Y-%m-%d-%H-%M-%S")
    timestamp_line = f"#{key} updated: {timestamp}"
    stamp_prefix = f"#{key} updated:"

    lines: list[str] = []
    found = False
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            stripped = line.lstrip()
            if stripped.startswith(stamp_prefix):
                continue
            if stripped.startswith(f"export {key}=") or line.startswith(f"{key}="):
                prefix = "export " if stripped.startswith("export ") else ""
                lines.append(timestamp_line)
                lines.append(f"{prefix}{key}={value}")
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


SESSION_REQUIRED_POLICIES: list[str] = []  # relaxed 2026-05: no longer required
TOKEN_PAGE_TIMEOUT_SECONDS = 3600
POST_LOGOUT_HOLD_SECONDS = 5
CALLBACK_HTML_TEMPLATE = Template(
    """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Loading Token Details</title>
  <meta http-equiv="refresh" content="0; url=$redirect_uri">
  <style>
    body {
      margin: 0;
      background: #ffffff;
    }
  </style>
</head>
<body>
  <script>
    window.addEventListener("load", function () {
      window.location.replace("$redirect_uri");
    });
  </script>
  <noscript>
    <meta http-equiv="refresh" content="0; url=$redirect_uri">
  </noscript>
</body>
</html>
"""
)


class FacilityAPIAuthError(Exception):
    """Raised when authentication state is missing or invalid."""


class _TokenPageServer(HTTPServer):
    def __init__(self, server_address: tuple[str, int], html_state: dict[str, object]) -> None:
        super().__init__(server_address, _TokenPageHandler)
        self.html_state = html_state


class _TokenPageHandler(BaseHTTPRequestHandler):
    server: _TokenPageServer

    def do_GET(self) -> None:
        body = self.server.html_state["html"].encode("utf-8")
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.send_header("Content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/logout":
            self.send_response(404)
            self.end_headers()
            return

        self.server.html_state["logged_out"] = True
        _remove_env_keys([*ENV_VARS, *RETIRED_ENV_VARS, REFRESH_VAR, EXPIRES_VAR])
        self.server.html_state["html"] = _build_logged_out_html()
        self.server.html_state["hold_until"] = time.time() + POST_LOGOUT_HOLD_SECONDS
        body = json.dumps({"ok": True}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _build_client() -> globus_sdk.NativeAppAuthClient:
    return globus_sdk.NativeAppAuthClient(client_id=AUTH_CLIENT_ID, app_name=APP_NAME)


def _fetch_user_profile(access_token: str) -> dict:
    auth_client = globus_sdk.AuthClient(authorizer=globus_sdk.AccessTokenAuthorizer(access_token))
    userinfo = auth_client.oauth2_userinfo().data
    identity_id = userinfo.get("sub")

    profile = {
        "preferred_username": userinfo.get("preferred_username"),
        "name": userinfo.get("name"),
        "email": userinfo.get("email"),
        "identity_id": identity_id,
        "userinfo": userinfo,
    }

    if identity_id:
        try:
            identities = auth_client.get_identities(ids=[identity_id]).data.get("identities", [])
            if identities:
                identity = identities[0]
                profile["username"] = identity.get("username")
                profile["name"] = identity.get("name", profile.get("name"))
                profile["email"] = identity.get("email", profile.get("email"))
                profile["organization"] = identity.get("organization")
                profile["identity_provider"] = identity.get("identity_provider")
        except Exception:
            pass

    return profile


def _get_auth_access_token(token_response: globus_sdk.OAuthTokenResponse) -> str | None:
    by_rs = token_response.by_resource_server
    for key in ("auth.globus.org", "transfer.api.globus.org", SCOPE_CLIENT_ID):
        token_data = by_rs.get(key)
        if token_data and token_data.get("access_token"):
            return token_data["access_token"]
    return None


def _auth_params() -> GlobusAuthorizationParameters:
    return GlobusAuthorizationParameters(
        required_scopes=[*AUTH_SCOPES, SCOPE_STRING],
        session_required_policies=SESSION_REQUIRED_POLICIES,
        prompt="login",
    )


def _extract_token_data(token_response: globus_sdk.OAuthTokenResponse) -> dict:
    by_rs = token_response.by_resource_server
    if SCOPE_CLIENT_ID not in by_rs:
        raise FacilityAPIAuthError(
            f"No token for resource server {SCOPE_CLIENT_ID} was returned. "
            "Make sure the requested scope is correct."
        )
    token_data = dict(by_rs[SCOPE_CLIENT_ID])
    token_data["resource_server"] = SCOPE_CLIENT_ID
    token_data["scope_string"] = SCOPE_STRING
    profile_token = _get_auth_access_token(token_response) or token_data["access_token"]
    try:
        token_data["profile"] = _fetch_user_profile(profile_token)
    except Exception:
        token_data["profile"] = {}
    return token_data


def _save_token_data(token_data: dict) -> None:
    for _name in ENV_VARS:
        _update_env(_name, token_data["access_token"])
    if token_data.get("refresh_token"):
        _update_env(REFRESH_VAR, token_data["refresh_token"])
    if token_data.get("expires_at_seconds"):
        _update_env(EXPIRES_VAR, str(int(token_data["expires_at_seconds"])))


def _load_token_data() -> dict:
    data: dict = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            for key, attr in [
                (ENV_VAR, "access_token"),
                (REFRESH_VAR, "refresh_token"),
                (EXPIRES_VAR, "expires_at_seconds"),
            ]:
                if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
                    data[attr] = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
    if not data.get("access_token"):
        raise FacilityAPIAuthError(
            'No ALCF IRI token in .env. Run '
            '"python3 alcf_iri_token.py authenticate" first.'
        )
    if "expires_at_seconds" in data:
        data["expires_at_seconds"] = float(data["expires_at_seconds"])
    return data


def _build_waiting_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ALCF IRI Access Token</title>
  <meta http-equiv="refresh" content="0.2">
  <style>
    body {
      margin: 0;
      background: #ffffff;
    }
  </style>
</head>
<body>
  <script>
    window.setTimeout(function () {
      window.location.reload();
    }, 200);
  </script>
</body>
</html>
"""


def _build_logged_out_html() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Logged Out</title>
  <style>
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.12), transparent 32%),
        linear-gradient(180deg, #f7f9fc 0%, #eef3fb 100%);
      color: #0f172a;
    }
    .card {
      width: min(560px, calc(100vw - 2rem));
      background: rgba(255, 255, 255, 0.92);
      border: 1px solid rgba(148, 163, 184, 0.25);
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(15, 23, 42, 0.12);
      padding: 2.5rem;
      box-sizing: border-box;
    }
    .eyebrow {
      display: inline-block;
      font-size: 0.82rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #2563eb;
      font-weight: 700;
      margin-bottom: 0.9rem;
    }
    h1 {
      margin: 0 0 0.6rem;
      font-size: clamp(2rem, 5vw, 2.8rem);
      line-height: 1.05;
    }
    p {
      margin: 0;
      color: #475569;
      font-size: 1.05rem;
    }
    .actions {
      margin-top: 1.25rem;
      display: flex;
      align-items: center;
      gap: 0.75rem;
      flex-wrap: wrap;
    }
    button {
      border: 0;
      border-radius: 14px;
      padding: 0.85rem 1.15rem;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
      color: white;
      box-shadow: 0 14px 30px rgba(37, 99, 235, 0.28);
    }
    #countdown {
      font-weight: 700;
      color: #2563eb;
    }
    #close-status {
      color: #64748b;
      font-size: 0.95rem;
      min-height: 1.2rem;
    }
  </style>
</head>
<body>
  <main class="card">
    <div class="eyebrow">ALCF IRI</div>
    <h1>Logged out</h1>
    <p>Your local token cache has been cleared. You may close this tab when you are done.</p>
    <div class="actions">
      <button id="close-btn" type="button">Close tab</button>
      <span id="close-status" aria-live="polite"></span>
    </div>
  </main>
  <script>
    const closeStatus = document.getElementById("close-status");
    function tryClose() {
      window.close();
      window.setTimeout(() => {
        if (!window.closed) {
          closeStatus.textContent = "Auto-close was blocked by the browser. You can close this tab manually.";
        }
      }, 250);
    }
    document.getElementById("close-btn").addEventListener("click", tryClose);
  </script>
</body>
</html>
"""


def _start_token_page_server() -> tuple[_TokenPageServer, dict[str, object], str]:
    html_state: dict[str, object] = {
        "html": _build_waiting_html(),
        "hold_until": 0.0,
    }
    server = _TokenPageServer(("127.0.0.1", 0), html_state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    print(f"Server: http://{host}:{port}")
    return server, html_state, f"http://{host}:{port}"


def _render_token_ui_html(token_data: dict, token: str | None = None) -> str:
    token = token or token_data["access_token"]
    profile = token_data.get("profile", {})
    safe_token = html.escape(token)
    safe_name = html.escape(str(profile.get("name") or ""))
    safe_username = html.escape(str(profile.get("username") or profile.get("preferred_username") or ""))
    safe_email = html.escape(str(profile.get("email") or ""))
    safe_identity = html.escape(str(profile.get("identity_id") or ""))
    safe_org = html.escape(str(profile.get("organization") or ""))
    expires_at = token_data.get("expires_at_seconds")
    expires_str = ""
    if expires_at:
        expires_str = time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(expires_at))
    safe_expires = html.escape(expires_str)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ALCF IRI Globus Token</title>
  <style>
    :root {{
      --bg-top: #f8fbff;
      --bg-bottom: #edf3fb;
      --card: rgba(255, 255, 255, 0.9);
      --card-border: rgba(148, 163, 184, 0.26);
      --text: #0f172a;
      --muted: #475569;
      --line: #dbe4f0;
      --accent: #2563eb;
      --accent-dark: #1d4ed8;
      --textarea-bg: #f8fbff;
    }}
    * {{
      box-sizing: border-box;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      min-height: 100vh;
      background:
        radial-gradient(circle at top left, rgba(37, 99, 235, 0.12), transparent 28%),
        radial-gradient(circle at bottom right, rgba(59, 130, 246, 0.08), transparent 24%),
        linear-gradient(180deg, var(--bg-top) 0%, var(--bg-bottom) 100%);
      color: var(--text);
      line-height: 1.5;
    }}
    .shell {{
      width: min(980px, calc(100vw - 2rem));
      margin: 2rem auto;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 28px;
      box-shadow: 0 24px 70px rgba(15, 23, 42, 0.12);
      backdrop-filter: blur(10px);
      padding: 2rem;
    }}
    .eyebrow {{
      display: inline-block;
      font-size: 0.8rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 0.85rem;
    }}
    textarea {{
      width: 100%;
      min-height: 16rem;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.95rem;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 1rem;
      background: var(--textarea-bg);
      color: var(--text);
      box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    button {{
      border: 0;
      border-radius: 14px;
      padding: 0.85rem 1.15rem;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
    }}
    button:hover {{
      transform: translateY(-1px);
    }}
    button.secondary {{
      background: linear-gradient(180deg, #ffffff 0%, #f8fbff 100%);
      border: 1px solid #94a3b8;
      color: #0f172a;
      box-shadow: 0 14px 30px rgba(15, 23, 42, 0.12);
    }}
    button.secondary:hover {{
      background: linear-gradient(180deg, #ffffff 0%, #eef4ff 100%);
      box-shadow: 0 18px 36px rgba(15, 23, 42, 0.16);
    }}
    #copy-btn {{
      background: linear-gradient(135deg, var(--accent) 0%, var(--accent-dark) 100%);
      color: white;
      box-shadow: 0 14px 30px rgba(37, 99, 235, 0.28);
    }}
    .topbar {{
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    .topbar h1 {{
      margin: 0;
      font-size: clamp(2.2rem, 5vw, 3.2rem);
      line-height: 0.98;
      letter-spacing: -0.03em;
    }}
    #logout-btn-top {{
      min-width: 140px;
      min-height: 54px;
      font-size: 1rem;
      font-weight: 700;
    }}
    .hint {{
      color: var(--muted);
      font-size: 1.05rem;
      margin: 0 0 1.5rem;
    }}
    dl {{
      display: grid;
      grid-template-columns: 170px 1fr;
      gap: 0.7rem 1rem;
      margin: 0 0 1.75rem;
      padding: 1.25rem;
      border: 1px solid var(--line);
      border-radius: 20px;
      background: rgba(248, 251, 255, 0.75);
    }}
    dt {{
      color: var(--muted);
      font-weight: 600;
    }}
    dd {{
      margin: 0;
      word-break: break-word;
      font-weight: 500;
    }}
    .actions {{
      display: flex;
      align-items: center;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }}
    #status {{
      color: var(--muted);
      min-height: 1.5rem;
    }}
    @media (max-width: 640px) {{
      .shell {{
        width: calc(100vw - 1rem);
        margin: 0.5rem auto;
      }}
      .card {{
        border-radius: 20px;
        padding: 1.2rem;
      }}
      .topbar {{
        flex-direction: column;
        align-items: stretch;
      }}
      .topbar button {{
        width: 100%;
      }}
      dl {{
        grid-template-columns: 1fr;
      }}
      .actions {{
        flex-direction: column;
        align-items: stretch;
      }}
    }}
  </style>
</head>
<body>
  <main class="shell">
    <section class="card">
      <div class="eyebrow">ALCF IRI</div>
      <div class="topbar">
        <h1>Access Token</h1>
        <button id="logout-btn-top" class="secondary" type="button">Logout</button>
      </div>
      <p class="hint">Use the button below to copy the token into your clipboard.</p>
      <dl>
        <dt>Name</dt><dd>{safe_name}</dd>
        <dt>Username</dt><dd>{safe_username}</dd>
        <dt>Email</dt><dd>{safe_email}</dd>
        <dt>Identity ID</dt><dd>{safe_identity}</dd>
        <dt>Organization</dt><dd>{safe_org}</dd>
        <dt>Expires</dt><dd>{safe_expires}</dd>
      </dl>
      <div class="actions">
        <button id="copy-btn" type="button">Copy token</button>
        <span id="status" aria-live="polite"></span>
      </div>
      <textarea id="token" readonly>{safe_token}</textarea>
    </section>
  </main>
  <script>
    const token = document.getElementById("token");
    const status = document.getElementById("status");
    async function logout() {{
      status.textContent = "Logging out...";
      try {{
        const response = await fetch("/logout", {{ method: "POST" }});
        if (!response.ok) {{
          throw new Error(`HTTP ${{response.status}}`);
        }}
        status.textContent = "Logged out.";
        token.value = "";
        window.setTimeout(() => window.location.reload(), 150);
      }} catch (err) {{
        status.textContent = "Logout failed. Use the CLI logout command.";
      }}
    }}
    document.getElementById("copy-btn").addEventListener("click", async () => {{
      token.select();
      token.setSelectionRange(0, token.value.length);
      try {{
        await navigator.clipboard.writeText(token.value);
        status.textContent = "Token copied.";
      }} catch (err) {{
        status.textContent = "Clipboard copy failed. Use Cmd/Ctrl+C.";
      }}
    }});
    document.getElementById("logout-btn-top").addEventListener("click", logout);
  </script>
</body>
</html>
"""


def _serve_token_page(
    token_data: dict,
    token_server: _TokenPageServer | None = None,
    token_page_state: dict[str, object] | None = None,
) -> None:
    owns_server = token_server is None or token_page_state is None
    if owns_server:
        token_server, token_page_state, _token_page_uri = _start_token_page_server()
    assert token_server is not None
    assert token_page_state is not None
    token_page_state["html"] = _render_token_ui_html(token_data, token_data["access_token"])
    print("Token page server is running. Use the page Logout button or press Ctrl+C to stop it.")
    deadline = time.time() + TOKEN_PAGE_TIMEOUT_SECONDS
    try:
        while time.time() < deadline:
            hold_until = float(token_page_state.get("hold_until", 0.0))
            if token_page_state.get("logged_out") and time.time() >= hold_until:
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        token_server.shutdown()
        token_server.server_close()


def _browser_available() -> bool:
    """True if a runnable web browser exists.

    globus_sdk's LocalServerLoginFlowManager calls ``webbrowser.get()`` (which
    raises ``webbrowser.Error`` on a headless host) *before* our monkeypatch of
    ``webbrowser.open`` can intercept anything — so we must probe up front and
    pick the command-line flow when no browser is present.
    """
    if os.environ.get("BROWSER"):
        return True
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        # Headless Linux server (e.g. trinity-host): no X display, no browser.
        return False
    try:
        webbrowser.get()
        return True
    except Exception:
        return False


def authenticate(force_reauth: bool = False, **_legacy: object) -> dict:
    """
    Authenticate against the ALCF IRI API.

    Picks the login flow to match the environment (browser desktop, headless
    SSH terminal, or non-interactive) — see the body for details.

    - Reuses a valid cached token by default.
    - On a fresh login, prints the OAuth URL and waits for the localhost
      callback (the user must open the URL manually); never auto-launches a
      browser and never serves the post-auth HTML token page.
    - Writes the token to the project .env under every name in ENV_VARS
      (with a timestamp comment) and prints the access token to stdout
      when done.

    `**_legacy` swallows obsolete kwargs (e.g. `open_token_page`) so older
    callers don't break.
    """
    print(f"\n=== Authenticating ALCF IRI token ({ENV_VAR}) ===")

    if not force_reauth:
        try:
            token_data = get_token_data(force_refresh=False)
            print(f"Using existing token from {ENV_FILE}")
            _emit_token(token_data)
            return token_data
        except FacilityAPIAuthError:
            pass

    client = _build_client()

    # Choose a login flow that fits the environment:
    #   * browser + interactive TTY (a local desktop) -> LocalServerLoginFlowManager
    #     (auto-catches the localhost OAuth callback).
    #   * headless but interactive (SSH terminal, no browser) -> CommandLineLoginFlowManager
    #     (prints the URL, reads the pasted auth code — no browser, no callback).
    #   * non-interactive (agent-invoked, no TTY) -> refuse with actionable guidance
    #     instead of crashing on webbrowser.get() or hanging on input().
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    can_browser = _browser_available()

    if not interactive:
        raise FacilityAPIAuthError(
            "Interactive OAuth login is not possible here (no TTY). On a server, "
            "run `python3 alcf_iri_token.py ensure_valid` to refresh from the "
            f"stored refresh token, or set {ENV_VAR} directly in the "
            f"environment / {ENV_FILE}."
        )

    if can_browser:
        flow = LocalServerLoginFlowManager(client, request_refresh_tokens=True)
        # Belt-and-suspenders: if the SDK falls back to webbrowser.open, print
        # the URL rather than silently failing to open a tab.
        _original_webbrowser_open = webbrowser.open
        def _print_only(url, *args, **kwargs):
            print(f"\nOpen this URL in your browser to authenticate:\n  {url}\n")
            return True
        webbrowser.open = _print_only
        try:
            token_response = flow.run_login_flow(_auth_params())
        finally:
            webbrowser.open = _original_webbrowser_open
    else:
        if CommandLineLoginFlowManager is None:
            raise FacilityAPIAuthError(
                "No usable web browser was found and this globus_sdk lacks "
                "CommandLineLoginFlowManager. Upgrade globus_sdk, or run "
                "`ensure_valid` to refresh from the stored refresh token."
            )
        # Manual flow: prints the URL, waits for the pasted auth code. Works
        # over SSH — the user opens the URL on their own machine.
        flow = CommandLineLoginFlowManager(client, request_refresh_tokens=True)
        token_response = flow.run_login_flow(_auth_params())

    token_data = _extract_token_data(token_response)
    _save_token_data(token_data)

    print(f"ALCF IRI tokens saved to {ENV_FILE}")
    _emit_token(token_data)
    return token_data


def _emit_token(token_data: dict) -> None:
    """Write the access token to .env and print it to stdout."""
    token = token_data["access_token"]
    for name in ENV_VARS:
        _update_env(name, token)
        os.environ[name] = token
    print(f"{' and '.join(ENV_VARS)} written to {ENV_FILE}")
    print(f"\n{ENV_VAR}:")
    print(token)


def _refresh_token_data(token_data: dict) -> dict:
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise FacilityAPIAuthError(
            "No refresh token is stored. Re-run "
            '"python3 alcf_iri_token.py authenticate".'
        )

    client = _build_client()
    refreshed = client.oauth2_refresh_token(refresh_token)
    refreshed_data = _extract_token_data(refreshed)
    _save_token_data(refreshed_data)
    return refreshed_data


def get_token_data(force_refresh: bool = False) -> dict:
    token_data = _load_token_data()
    expires_at = token_data.get("expires_at_seconds", 0)
    now = time.time()

    # Refresh slightly ahead of expiry to avoid handing back a token that is
    # about to die while being copied into another tool.
    if force_refresh or expires_at <= now + 60:
        token_data = _refresh_token_data(token_data)
    return token_data


def get_access_token(force_refresh: bool = False) -> str:
    return get_token_data(force_refresh=force_refresh)["access_token"]


def get_time_until_token_expiration(units: str = "seconds") -> float:
    token_data = get_token_data(force_refresh=False)
    delta_t = token_data["expires_at_seconds"] - time.time()

    if units == "minutes":
        delta_t /= 60
    elif units == "hours":
        delta_t /= 3600
    elif units != "seconds":
        raise FacilityAPIAuthError("Units must be 'seconds', 'minutes', or 'hours'.")

    return round(delta_t, 2)


def logout() -> None:
    """
    Remove ALCF IRI tokens from .env. Remote revocation is intentionally not
    attempted here.
    """
    _remove_env_keys([*ENV_VARS, *RETIRED_ENV_VARS, REFRESH_VAR, EXPIRES_VAR])
    print(f"ALCF IRI tokens removed from {ENV_FILE}")


def ensure_valid() -> bool:
    """
    Non-interactive: refresh the cached token if close to expiry and write it
    to .env under every name in ENV_VARS. Returns True on success, False if no
    cached token is present or the refresh fails (caller should run
    `authenticate`).
    """
    try:
        token_data = get_token_data(force_refresh=False)
    except FacilityAPIAuthError as exc:
        print(f"ALCF IRI ensure_valid failed: {exc}")
        return False
    except Exception as exc:
        print(f"ALCF IRI ensure_valid failed: {exc}")
        return False
    _emit_token(token_data)
    return True


def show_token_ui(
    token: str | None = None,
    target_path: pathlib.Path | None = None,
    open_browser: bool = True,
) -> pathlib.Path:
    """
    Open a small local HTML page that shows the token in a textarea and provides
    a copy button.
    """
    token_data = get_token_data(force_refresh=False)
    html_body = _render_token_ui_html(token_data, token)

    if target_path is None:
        fd, tmp_path = tempfile.mkstemp(prefix="alcf-iri-token-", suffix=".html")
        os.close(fd)
        target_path = pathlib.Path(tmp_path)

    target_path.write_text(html_body, encoding="utf-8")
    if open_browser:
        webbrowser.open(target_path.as_uri())
        print(f"Opened token page: {target_path}")
    else:
        print(f"Updated token page: {target_path}")
    return target_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action",
        nargs="?",
        default="ensure_valid",
        choices=["authenticate", "ensure_valid", "get_access_token", "get_time_until_token_expiration", "show_token_ui", "logout"],
        help="Action to perform (default: ensure_valid).",
    )
    parser.add_argument(
        "--units",
        choices=["seconds", "minutes", "hours"],
        default="seconds",
        help="Units for the time until token expiration.",
    )
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="Refresh the stored token before returning it.",
    )
    parser.add_argument(
        "--force-reauth",
        action="store_true",
        help="Force a fresh browser login even if a valid cached token already exists.",
    )
    args = parser.parse_args()

    if args.action == "authenticate":
        authenticate(force_reauth=args.force_reauth)
    elif args.action == "ensure_valid":
        ok = ensure_valid()
        raise SystemExit(0 if ok else 1)
    elif args.action == "get_access_token":
        print(get_access_token(force_refresh=args.force_refresh))
    elif args.action == "get_time_until_token_expiration":
        print(get_time_until_token_expiration(args.units))
    elif args.action == "show_token_ui":
        token = get_access_token(force_refresh=args.force_refresh)
        show_token_ui(token)
    elif args.action == "logout":
        logout()


if __name__ == "__main__":
    main()
