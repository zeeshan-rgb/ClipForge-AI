"""Sending permission requests from the owner's own Gmail, and noticing replies.

It reuses the OAuth client the owner already gave ClipForge for YouTube (the
"Desktop app" client from their own Google Cloud project), with its own sign-in
and its own token, kept in the data folder:

* `gmail.send`     - to send the requests and one follow-up;
* `gmail.readonly` - only to read the threads ClipForge started, to see a reply.

The owner signs in on Google's own page; ClipForge never sees the password.
The Gmail API must be enabled in the same Google Cloud project.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.parse
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path
from typing import Any

import httpx

from . import youtube as yt

SCOPES = ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
API = "https://gmail.googleapis.com/gmail/v1/users/me"


class GmailError(RuntimeError):
    """Something went wrong talking to Gmail (worth retrying later)."""


class Refused(GmailError):
    """Will not work until the owner does something."""


class NotConnected(Refused):
    pass


def make_http() -> httpx.Client:
    """The HTTP client for Google. Tests replace this with a stand-in."""
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=20.0), follow_redirects=False)


def gmail_dir(settings) -> Path:
    return settings.data_dir / "gmail"


def token_file(settings) -> Path:
    return gmail_dir(settings) / "token.json"


def load_token(settings) -> dict[str, Any] | None:
    path = token_file(settings)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def save_token(settings, token: dict[str, Any]) -> None:
    gmail_dir(settings).mkdir(parents=True, exist_ok=True)
    yt._write_private(token_file(settings), json.dumps(token, indent=2))


def client(settings) -> yt.ClientInfo:
    path = yt.client_file(settings)
    if not path.is_file():
        raise Refused("Add the Google Cloud client file first (Settings → YouTube channel); Gmail uses the same one.")
    try:
        return yt.read_client(path)
    except yt.Refused as exc:
        raise Refused(str(exc)) from exc


def auth_url(info: yt.ClientInfo, redirect_uri: str, state: str, challenge: str) -> str:
    query = {
        "client_id": info.client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(SCOPES), "state": state, "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256",
    }
    return yt.AUTH_URL + "?" + urllib.parse.urlencode(query)


def exchange_code(http: httpx.Client, info: yt.ClientInfo, code: str, verifier: str, redirect_uri: str) -> dict:
    resp = http.post(yt.TOKEN_URL, data={
        "code": code, "client_id": info.client_id, "client_secret": info.client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier,
    })
    if resp.status_code != 200:
        raise Refused(f"Google did not accept the sign-in: {yt._google_error(resp)}")
    data = resp.json()
    if not data.get("refresh_token"):
        raise Refused("Google gave no lasting access. Remove ClipForge's access in your Google account and connect again.")
    granted = set((data.get("scope") or "").split())
    missing = [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted]
    if missing:
        raise Refused("Not all access was granted (missing: " + ", ".join(missing) + "). Connect again and tick "
                      "both boxes: sending email, and reading it so replies to the requests can be noticed.")
    return {"refresh_token": data["refresh_token"], "access_token": data["access_token"],
            "expires_at": time.time() + float(data.get("expires_in", 3600)), "scope": data.get("scope")}


def access_token(settings, http: httpx.Client) -> str:
    token = load_token(settings)
    if not token or not token.get("refresh_token"):
        raise NotConnected("Gmail is not connected. Connect it on the Permissions page.")
    if token.get("access_token") and float(token.get("expires_at", 0)) > time.time() + 60:
        return token["access_token"]
    info = client(settings)
    resp = http.post(yt.TOKEN_URL, data={"client_id": info.client_id, "client_secret": info.client_secret,
                                         "refresh_token": token["refresh_token"], "grant_type": "refresh_token"})
    if resp.status_code in (400, 401) and "invalid_grant" in resp.text:
        raise NotConnected("Gmail access was withdrawn or has expired. Connect Gmail again on the Permissions page. "
                           "(While the Google Cloud app is in Testing, Google ends access after 7 days.)")
    if resp.status_code != 200:
        raise GmailError(f"Could not renew access to Gmail: {yt._google_error(resp)}")
    data = resp.json()
    token.update(access_token=data["access_token"], expires_at=time.time() + float(data.get("expires_in", 3600)))
    save_token(settings, token)
    return token["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def fetch_address(http: httpx.Client, token: str) -> str:
    resp = http.get(f"{API}/profile", headers=_auth(token))
    if resp.status_code == 403 and ("has not been used" in resp.text or "SERVICE_DISABLED" in resp.text):
        raise Refused("The Gmail API is not enabled in your Google Cloud project. Enable it, then connect again.")
    if resp.status_code != 200:
        raise GmailError(f"Could not read your Gmail address: {yt._google_error(resp)}")
    return resp.json()["emailAddress"]


def revoke(settings, http: httpx.Client) -> None:
    token = load_token(settings)
    if token and token.get("refresh_token"):
        try:
            http.post(yt.REVOKE_URL, data={"token": token["refresh_token"]})
        except httpx.HTTPError:
            pass
    token_file(settings).unlink(missing_ok=True)


# ---- sending and reading ---------------------------------------------------------------------------


def build_message(sender: str, sender_name: str, to: str, subject: str, body: str,
                  in_reply_to: str | None = None) -> tuple[str, str]:
    """The raw message for the API, and its Message-ID."""
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
    msg["To"] = to
    msg["Subject"] = subject
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else "clipforge.local"
    msg_id = make_msgid(domain=domain)
    msg["Message-ID"] = msg_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode(), msg_id


def send(settings, http: httpx.Client, *, to: str, subject: str, body: str, sender_name: str,
         thread_id: str | None = None, in_reply_to: str | None = None) -> dict[str, str]:
    """Send one email. Returns Gmail's message and thread ids and the Message-ID header."""
    token = access_token(settings, http)
    sender = (load_token(settings) or {}).get("address") or ""
    raw, msg_id = build_message(sender, sender_name, to, subject, body, in_reply_to)
    payload: dict[str, Any] = {"raw": raw}
    if thread_id:
        payload["threadId"] = thread_id
    resp = http.post(f"{API}/messages/send", headers=_auth(token), json=payload)
    if resp.status_code == 429 or "rateLimitExceeded" in resp.text or "dailyLimitExceeded" in resp.text:
        raise GmailError("Gmail's sending limit was reached; ClipForge will try again later.")
    if resp.status_code in (400, 403) and ("invalidArgument" in resp.text or "Invalid To header" in resp.text):
        raise Refused(f"Gmail refused the address {to!r}: {yt._google_error(resp)}")
    if resp.status_code != 200:
        raise GmailError(f"Gmail did not send the message: {yt._google_error(resp)}")
    data = resp.json()
    return {"message_id": data["id"], "thread_id": data.get("threadId") or data["id"], "header_id": msg_id}


def _header(headers: list[dict], name: str) -> str:
    for h in headers or []:
        if (h.get("name") or "").lower() == name.lower():
            return h.get("value") or ""
    return ""


def _text(payload: dict) -> str:
    """The plain-text body of a message (first text/plain part)."""
    if (payload.get("mimeType") or "").startswith("text/plain"):
        data = (payload.get("body") or {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    for part in payload.get("parts") or []:
        found = _text(part)
        if found:
            return found
    return ""


def read_thread(settings, http: httpx.Client, thread_id: str) -> list[dict[str, Any]]:
    """Every message in a thread: who sent it, when, a snippet and its plain text."""
    token = access_token(settings, http)
    resp = http.get(f"{API}/threads/{thread_id}", params={"format": "full"}, headers=_auth(token))
    if resp.status_code == 404:
        return []
    if resp.status_code != 200:
        raise GmailError(f"Could not read the email thread: {yt._google_error(resp)}")
    out = []
    for m in resp.json().get("messages") or []:
        payload = m.get("payload") or {}
        headers = payload.get("headers") or []
        out.append({
            "id": m.get("id"),
            "from": _header(headers, "From"),
            "address": parseaddr(_header(headers, "From"))[1].lower(),
            "subject": _header(headers, "Subject"),
            "date": _header(headers, "Date"),
            "message_id": _header(headers, "Message-ID"),
            "internal_ms": int(m.get("internalDate") or 0),
            "snippet": m.get("snippet") or "",
            "text": _text(payload)[:20000],
            "labels": m.get("labelIds") or [],
        })
    return out
