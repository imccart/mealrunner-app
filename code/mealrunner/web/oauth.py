"""OAuth 2.1 authorization server for the MCP endpoint.

MealRunner acts as both the MCP resource server (at /mcp) and its
authorization server. This module owns everything OAuth-shaped:
Dynamic Client Registration (RFC 7591), the .well-known metadata
documents (RFC 8414 / RFC 9728), the authorization + token endpoints,
and helpers to mint / validate access + refresh tokens.

The design is deliberately minimal and standards-conformant enough for
Claude connectors to auto-register and complete the authorization code
flow. We only speak `authorization_code` and `refresh_token` grants,
only PKCE with S256, and audience-bind every token to the MCP endpoint
via the `resource` parameter (RFC 8707).

Consent UI note: /oauth/authorize checks the caller's session cookie —
if the user isn't logged in to MealRunner, they get a login prompt page
that links back into the app. Once logged in they hit the consent screen
and can approve.

Tokens: opaque random strings, sha256-hashed at rest. Refresh tokens
rotate on every use per OAuth 2.1.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import text

from mealrunner.database import get_request_connection


router = APIRouter()


# ── Config ──────────────────────────────────────────────

_ACCESS_TOKEN_TTL = timedelta(hours=1)
_REFRESH_TOKEN_TTL = timedelta(days=30)
_AUTH_CODE_TTL = timedelta(minutes=10)


def _public_base_url(request: Request) -> str:
    """Return the canonical base URL clients use to reach us. Env var wins
    (Railway sets it), otherwise derive from the request. Never trailing-
    slashed and always exactly one origin."""
    env = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if env:
        return env
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.hostname or "getmealrunner.app"
    return f"{scheme}://{host}"


def _mcp_resource_uri(request: Request) -> str:
    return _public_base_url(request) + "/mcp"


# ── Hashing helpers ─────────────────────────────────────

def _sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Client registration ─────────────────────────────────

def _register_client(conn, client_name: str, redirect_uris: list[str], is_public: bool) -> dict:
    """Create a client and return {client_id, client_secret, ...}. Public
    clients (PKCE-only, no confidential storage) get no secret."""
    client_id = "mcp_client_" + uuid.uuid4().hex[:16]
    client_secret = None
    client_secret_hash = None
    if not is_public:
        client_secret = secrets.token_urlsafe(32)
        client_secret_hash = _sha(client_secret)
    conn.execute(
        text("""INSERT INTO oauth_clients (client_id, client_secret_hash, client_name, redirect_uris)
                VALUES (:cid, :csh, :name, :uris)"""),
        {"cid": client_id, "csh": client_secret_hash,
         "name": (client_name or "").strip()[:200],
         "uris": json.dumps(redirect_uris)},
    )
    conn.commit()
    return {"client_id": client_id, "client_secret": client_secret,
            "client_name": client_name, "redirect_uris": redirect_uris}


def _get_client(conn, client_id: str) -> dict | None:
    row = conn.execute(
        text("SELECT client_id, client_secret_hash, client_name, redirect_uris FROM oauth_clients WHERE client_id = :cid"),
        {"cid": client_id},
    ).fetchone()
    if not row:
        return None
    return {
        "client_id": row["client_id"],
        "client_secret_hash": row["client_secret_hash"],
        "client_name": row["client_name"],
        "redirect_uris": json.loads(row["redirect_uris"] or "[]"),
    }


def _verify_client_secret(client: dict, presented_secret: str | None) -> bool:
    stored = client.get("client_secret_hash")
    if stored is None:
        # Public client — no secret required (PKCE handles proof of possession).
        return True
    if not presented_secret:
        return False
    return _sha(presented_secret) == stored


# ── Auth code ───────────────────────────────────────────

def _create_auth_code(conn, *, client_id: str, user_id: str, redirect_uri: str,
                       code_challenge: str, code_challenge_method: str,
                       resource: str, scope: str) -> str:
    code = secrets.token_urlsafe(32)
    conn.execute(
        text("""INSERT INTO oauth_auth_codes
                (code_hash, client_id, user_id, redirect_uri, code_challenge,
                 code_challenge_method, resource, scope, expires_at)
                VALUES (:h, :cid, :uid, :ruri, :chal, :meth, :res, :sc, :exp)"""),
        {"h": _sha(code), "cid": client_id, "uid": user_id,
         "ruri": redirect_uri, "chal": code_challenge,
         "meth": code_challenge_method, "res": resource, "sc": scope,
         "exp": _now() + _AUTH_CODE_TTL},
    )
    conn.commit()
    return code


def _consume_auth_code(conn, code: str) -> dict | None:
    """Atomically read and mark an auth code as used. Returns the code
    metadata dict, or None if unknown/expired/already-used."""
    row = conn.execute(
        text("""SELECT client_id, user_id, redirect_uri, code_challenge,
                       code_challenge_method, resource, scope, expires_at, used_at
                FROM oauth_auth_codes WHERE code_hash = :h"""),
        {"h": _sha(code)},
    ).fetchone()
    if not row:
        return None
    if row["used_at"] is not None:
        return None
    expires_at = row["expires_at"]
    if expires_at is not None:
        # Postgres TIMESTAMPTZ comes back tz-aware; the psycopg2 fallback
        # path can return naive datetimes though, so normalize first.
        exp_utc = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        if exp_utc < _now():
            return None
    conn.execute(
        text("UPDATE oauth_auth_codes SET used_at = CURRENT_TIMESTAMP WHERE code_hash = :h"),
        {"h": _sha(code)},
    )
    conn.commit()
    return dict(row)


# ── Access + refresh tokens ─────────────────────────────

def _create_token_pair(conn, *, client_id: str, user_id: str, scope: str, audience: str) -> dict:
    access_token = "mra_" + secrets.token_urlsafe(32)
    refresh_token = "mrr_" + secrets.token_urlsafe(32)
    conn.execute(
        text("""INSERT INTO oauth_access_tokens
                (token_hash, client_id, user_id, scope, audience, expires_at)
                VALUES (:h, :cid, :uid, :sc, :aud, :exp)"""),
        {"h": _sha(access_token), "cid": client_id, "uid": user_id,
         "sc": scope, "aud": audience, "exp": _now() + _ACCESS_TOKEN_TTL},
    )
    conn.execute(
        text("""INSERT INTO oauth_refresh_tokens
                (token_hash, client_id, user_id, scope, audience, expires_at)
                VALUES (:h, :cid, :uid, :sc, :aud, :exp)"""),
        {"h": _sha(refresh_token), "cid": client_id, "uid": user_id,
         "sc": scope, "aud": audience, "exp": _now() + _REFRESH_TOKEN_TTL},
    )
    conn.commit()
    return {"access_token": access_token, "refresh_token": refresh_token,
            "expires_in": int(_ACCESS_TOKEN_TTL.total_seconds())}


def resolve_access_token(conn, plaintext: str, expected_audience: str | None = None) -> str | None:
    """Return the user_id for a valid access token, or None. Optionally
    enforces audience — the MCP endpoint SHOULD pass its own URI to prevent
    tokens issued for other resources from being accepted here."""
    if not plaintext:
        return None
    row = conn.execute(
        text("""SELECT user_id, audience, expires_at FROM oauth_access_tokens
                WHERE token_hash = :h AND revoked_at IS NULL"""),
        {"h": _sha(plaintext)},
    ).fetchone()
    if not row:
        return None
    exp = row["expires_at"]
    if exp is not None:
        exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if exp_utc < _now():
            return None
    if expected_audience and row["audience"] and row["audience"] != expected_audience:
        return None
    try:
        conn.execute(
            text("UPDATE oauth_access_tokens SET last_used_at = CURRENT_TIMESTAMP WHERE token_hash = :h"),
            {"h": _sha(plaintext)},
        )
        conn.commit()
    except Exception:
        pass
    return row["user_id"]


def _rotate_refresh_token(conn, plaintext: str) -> dict | None:
    """Consume a refresh token and issue a fresh access+refresh pair. Marks
    the old refresh token as rotated (single-use) per OAuth 2.1."""
    row = conn.execute(
        text("""SELECT token_hash, client_id, user_id, scope, audience, expires_at, rotated_at, revoked_at
                FROM oauth_refresh_tokens WHERE token_hash = :h"""),
        {"h": _sha(plaintext)},
    ).fetchone()
    if not row or row["revoked_at"] is not None or row["rotated_at"] is not None:
        return None
    exp = row["expires_at"]
    if exp is not None:
        exp_utc = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        if exp_utc < _now():
            return None
    conn.execute(
        text("UPDATE oauth_refresh_tokens SET rotated_at = CURRENT_TIMESTAMP WHERE token_hash = :h"),
        {"h": row["token_hash"]},
    )
    return _create_token_pair(
        conn, client_id=row["client_id"], user_id=row["user_id"],
        scope=row["scope"], audience=row["audience"],
    )


# ── PKCE ────────────────────────────────────────────────

def _verify_pkce(verifier: str, challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(b64, challenge)


# ── .well-known metadata ────────────────────────────────

@router.get("/.well-known/oauth-protected-resource")
async def protected_resource_metadata(request: Request):
    """RFC 9728: tell clients which authorization server(s) issue tokens
    for this MCP endpoint. We are our own AS, so we point at ourselves."""
    base = _public_base_url(request)
    return JSONResponse({
        "resource": base + "/mcp",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": ["mealrunner"],
    })


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request):
    """RFC 8414: advertise our OAuth endpoints and supported flows."""
    base = _public_base_url(request)
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": base + "/oauth/authorize",
        "token_endpoint": base + "/oauth/token",
        "registration_endpoint": base + "/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic", "none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": ["mealrunner"],
    })


# ── Dynamic Client Registration (RFC 7591) ──────────────

@router.post("/oauth/register")
async def register_client(request: Request):
    """Public endpoint — any MCP client (Claude, Claude Desktop, whatever)
    posts a JSON body to auto-register itself. We give back a client_id
    (and secret if requested)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_request", "error_description": "Body must be JSON"}, status_code=400)

    redirect_uris = body.get("redirect_uris") or []
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri",
                             "error_description": "redirect_uris must be a non-empty array"}, status_code=400)
    # Enforce HTTPS or localhost per OAuth 2.1 §1.5. Claude Desktop uses
    # custom schemes (e.g. claude-desktop://oauth/callback) — allow any
    # non-http:// scheme so those work, but block plain http:// unless
    # localhost.
    for uri in redirect_uris:
        if not isinstance(uri, str):
            return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)
        low = uri.lower()
        if low.startswith("http://") and not (low.startswith("http://localhost") or low.startswith("http://127.0.0.1")):
            return JSONResponse({"error": "invalid_redirect_uri",
                                 "error_description": "Non-HTTPS redirect URIs must target localhost"}, status_code=400)

    client_name = body.get("client_name") or ""
    # `token_endpoint_auth_method: "none"` = public client (PKCE-only, no secret).
    auth_method = body.get("token_endpoint_auth_method", "client_secret_post")
    is_public = auth_method == "none"

    conn = get_request_connection()
    client = _register_client(conn, client_name, redirect_uris, is_public)

    resp: dict[str, Any] = {
        "client_id": client["client_id"],
        "client_name": client["client_name"],
        "redirect_uris": client["redirect_uris"],
        "token_endpoint_auth_method": "none" if is_public else "client_secret_post",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    if client["client_secret"]:
        resp["client_secret"] = client["client_secret"]
    return JSONResponse(resp, status_code=201)


# ── Authorization endpoint (consent) ────────────────────

def _consent_page_html(*, client_name: str, form_action: str, form_fields: dict[str, str],
                        user_email: str) -> str:
    """Minimal, branded consent page. All request params ride along in hidden
    form fields so the POST reconstructs the original authorize request."""
    hidden = "\n".join(
        f'    <input type="hidden" name="{k}" value="{v}">' for k, v in form_fields.items()
    )
    safe_client = (client_name or "This application").replace("<", "&lt;").replace(">", "&gt;")
    safe_email = user_email.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Connect to MealRunner</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #F5F0E8; color: #2C2420; margin: 0; padding: 40px 20px;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; box-sizing: border-box; }}
  .card {{ background: white; border: 1px solid #E8DDD0; border-radius: 14px;
          padding: 32px; max-width: 440px; width: 100%;
          box-shadow: 0 8px 24px rgba(44,36,32,0.08); }}
  h1 {{ font-family: 'Playfair Display', Georgia, serif; margin: 0 0 12px; font-size: 22px; color: #2C2420; }}
  p {{ color: #6B5D52; font-size: 14px; line-height: 1.5; margin: 8px 0; }}
  .who {{ font-size: 12px; color: #8B6F5E; margin-top: 4px; }}
  .actions {{ display: flex; gap: 10px; margin-top: 24px; }}
  button {{ flex: 1; padding: 12px; border-radius: 8px; font-family: inherit;
           font-size: 14px; font-weight: 600; cursor: pointer; border: none; }}
  .approve {{ background: #D4623A; color: white; }}
  .approve:hover {{ background: #C25630; }}
  .deny {{ background: none; color: #6B5D52; border: 1px solid #E8DDD0; }}
</style></head>
<body><div class="card">
  <h1>{safe_client} wants to connect to MealRunner</h1>
  <p>Approving will let this app view and change your meal plan, grocery list, and orders on your behalf.</p>
  <p class="who">Signed in as {safe_email}</p>
  <form method="post" action="{form_action}">
{hidden}
    <div class="actions">
      <button type="submit" name="decision" value="deny" class="deny">Cancel</button>
      <button type="submit" name="decision" value="approve" class="approve">Approve</button>
    </div>
  </form>
</div></body></html>"""


def _login_prompt_html(*, return_to: str) -> str:
    safe = return_to.replace("<", "&lt;").replace(">", "&gt;")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Sign in to MealRunner</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         background: #F5F0E8; color: #2C2420; margin: 0; padding: 40px 20px;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; box-sizing: border-box; }}
  .card {{ background: white; border: 1px solid #E8DDD0; border-radius: 14px;
          padding: 32px; max-width: 420px; text-align: center; }}
  h1 {{ font-family: 'Playfair Display', Georgia, serif; margin: 0 0 12px; font-size: 22px; }}
  p {{ color: #6B5D52; font-size: 14px; line-height: 1.5; }}
  a.btn {{ display: inline-block; margin-top: 16px; padding: 12px 20px;
          background: #D4623A; color: white; border-radius: 8px;
          text-decoration: none; font-weight: 600; font-size: 14px; }}
</style></head>
<body><div class="card">
  <h1>Sign in to MealRunner first</h1>
  <p>Open MealRunner, sign in, then return to this page and refresh.</p>
  <a class="btn" href="/app">Open MealRunner</a>
  <p style="margin-top:16px; font-size:12px; color:#8B6F5E;">Then reload this page: {safe}</p>
</div></body></html>"""


def _redirect_with_error(redirect_uri: str, *, error: str, description: str, state: str | None) -> RedirectResponse:
    params = {"error": error, "error_description": description}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


@router.get("/oauth/authorize")
async def authorize_get(request: Request):
    """Render the consent screen. Requires the user to have a live session
    cookie — this route bypasses AuthMiddleware because it lives outside
    /api/*, so we do the session check ourselves."""
    from mealrunner.web.auth import get_user_from_session, SESSION_COOKIE

    q = request.query_params
    client_id = q.get("client_id")
    redirect_uri = q.get("redirect_uri")
    response_type = q.get("response_type", "code")
    code_challenge = q.get("code_challenge")
    code_challenge_method = q.get("code_challenge_method", "S256")
    state = q.get("state")
    resource = q.get("resource", "")
    scope = q.get("scope", "mealrunner")

    if not client_id or not redirect_uri:
        return JSONResponse({"error": "invalid_request",
                             "error_description": "client_id and redirect_uri required"}, status_code=400)

    conn = get_request_connection()
    client = _get_client(conn, client_id)
    if not client:
        return JSONResponse({"error": "invalid_client"}, status_code=400)
    if redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    # After this point, errors ride the redirect URI so the client can catch them.
    if response_type != "code":
        return _redirect_with_error(redirect_uri, error="unsupported_response_type",
                                     description="Only 'code' is supported", state=state)
    if not code_challenge or code_challenge_method != "S256":
        return _redirect_with_error(redirect_uri, error="invalid_request",
                                     description="PKCE with S256 is required", state=state)

    # Session check — the user must be logged in to consent on their own behalf.
    session_id = request.cookies.get(SESSION_COOKIE)
    user_id = get_user_from_session(conn, session_id) if session_id else None
    if not user_id:
        return HTMLResponse(_login_prompt_html(return_to=str(request.url)), status_code=200)

    email_row = conn.execute(
        text("SELECT email FROM users WHERE id = :id"), {"id": user_id},
    ).fetchone()
    user_email = email_row["email"] if email_row else ""

    # Show consent screen. Post back to POST /oauth/authorize with the same params.
    form_fields = {
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": response_type, "code_challenge": code_challenge,
        "code_challenge_method": code_challenge_method,
        "resource": resource, "scope": scope,
    }
    if state:
        form_fields["state"] = state
    html = _consent_page_html(
        client_name=client["client_name"] or "An application",
        form_action="/oauth/authorize",
        form_fields=form_fields,
        user_email=user_email,
    )
    return HTMLResponse(html)


@router.post("/oauth/authorize")
async def authorize_post(request: Request):
    """User clicked Approve or Cancel on the consent screen. On approve,
    mint an auth code and redirect to the client's redirect_uri."""
    from mealrunner.web.auth import get_user_from_session, SESSION_COOKIE

    form = await request.form()
    decision = form.get("decision")
    client_id = form.get("client_id")
    redirect_uri = form.get("redirect_uri")
    code_challenge = form.get("code_challenge")
    code_challenge_method = form.get("code_challenge_method", "S256")
    resource = form.get("resource", "") or ""
    scope = form.get("scope", "mealrunner") or "mealrunner"
    state = form.get("state")

    conn = get_request_connection()
    client = _get_client(conn, client_id) if client_id else None
    if not client or redirect_uri not in client["redirect_uris"]:
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    if decision != "approve":
        return _redirect_with_error(redirect_uri, error="access_denied",
                                     description="User cancelled", state=state)

    session_id = request.cookies.get(SESSION_COOKIE)
    user_id = get_user_from_session(conn, session_id) if session_id else None
    if not user_id:
        return _redirect_with_error(redirect_uri, error="access_denied",
                                     description="Not authenticated", state=state)

    if not code_challenge:
        return _redirect_with_error(redirect_uri, error="invalid_request",
                                     description="PKCE code_challenge required", state=state)

    code = _create_auth_code(conn, client_id=client_id, user_id=user_id,
                              redirect_uri=redirect_uri, code_challenge=code_challenge,
                              code_challenge_method=code_challenge_method,
                              resource=resource, scope=scope)

    params = {"code": code}
    if state:
        params["state"] = state
    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(url=f"{redirect_uri}{sep}{urlencode(params)}", status_code=302)


# ── Token endpoint ──────────────────────────────────────

def _extract_client_auth(request: Request, form) -> tuple[str | None, str | None]:
    """Client ID + secret can come from Basic auth or form body."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(auth_header[len("Basic "):].strip()).decode("utf-8")
            if ":" in decoded:
                cid, secret = decoded.split(":", 1)
                return cid, secret
        except Exception:
            pass
    return form.get("client_id"), form.get("client_secret")


@router.post("/oauth/token")
async def token_endpoint(request: Request):
    form = await request.form()
    grant_type = form.get("grant_type")

    conn = get_request_connection()

    if grant_type == "authorization_code":
        code = form.get("code")
        code_verifier = form.get("code_verifier")
        redirect_uri = form.get("redirect_uri")
        client_id, client_secret = _extract_client_auth(request, form)
        resource = form.get("resource", "") or ""

        if not code or not code_verifier or not client_id or not redirect_uri:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        client = _get_client(conn, client_id)
        if not client:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if not _verify_client_secret(client, client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        code_data = _consume_auth_code(conn, code)
        if not code_data:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "Auth code unknown, expired, or already used"}, status_code=400)
        if code_data["client_id"] != client_id:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        if code_data["redirect_uri"] != redirect_uri:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "redirect_uri mismatch"}, status_code=400)
        if not _verify_pkce(code_verifier, code_data["code_challenge"], code_data["code_challenge_method"]):
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "PKCE verification failed"}, status_code=400)

        # Audience-bind the token to the resource requested at authorize time
        # (or, if the client re-asserts one at token time, prefer that if it
        # matches what was authorized).
        audience = code_data.get("resource") or resource or _mcp_resource_uri(request)
        if resource and code_data.get("resource") and resource != code_data["resource"]:
            return JSONResponse({"error": "invalid_target",
                                 "error_description": "resource does not match authorization"}, status_code=400)

        tokens = _create_token_pair(conn, client_id=client_id, user_id=code_data["user_id"],
                                     scope=code_data.get("scope", ""), audience=audience)
        return JSONResponse({
            "access_token": tokens["access_token"],
            "token_type": "Bearer",
            "expires_in": tokens["expires_in"],
            "refresh_token": tokens["refresh_token"],
            "scope": code_data.get("scope", ""),
        })

    if grant_type == "refresh_token":
        refresh_token = form.get("refresh_token")
        client_id, client_secret = _extract_client_auth(request, form)
        if not refresh_token or not client_id:
            return JSONResponse({"error": "invalid_request"}, status_code=400)

        client = _get_client(conn, client_id)
        if not client:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if not _verify_client_secret(client, client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        tokens = _rotate_refresh_token(conn, refresh_token)
        if not tokens:
            return JSONResponse({"error": "invalid_grant",
                                 "error_description": "Refresh token unknown, expired, or already used"}, status_code=400)
        return JSONResponse({
            "access_token": tokens["access_token"],
            "token_type": "Bearer",
            "expires_in": tokens["expires_in"],
            "refresh_token": tokens["refresh_token"],
        })

    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
