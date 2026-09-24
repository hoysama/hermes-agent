"""Hermes Cloud Mail client for the cloud-mail Worker API."""

import json
import os
import uuid
from typing import Any, Dict

import requests
from tools.registry import registry, tool_error


def _request(path: str, method: str = "GET", payload: Dict[str, Any] | None = None) -> str:
    base = os.getenv("HERMES_CLOUD_MAIL_URL", "").rstrip("/")
    token = os.getenv("HERMES_CLOUD_MAIL_TOKEN", "")
    if not base or not token:
        return tool_error("Cloud Mail is not configured: set HERMES_CLOUD_MAIL_URL and HERMES_CLOUD_MAIL_TOKEN.")
    try:
        response = requests.request(
            method,
            f"{base}/api/hermes/mail/{path.lstrip('/')}",
            headers={"X-Hermes-Mail-Token": token, "Accept": "application/json"},
            json=payload if method != "GET" else None,
            params=payload if method == "GET" else None,
            timeout=30,
        )
        if response.status_code >= 400:
            return tool_error(f"Cloud Mail request failed ({response.status_code}): {response.text[:500]}")
        return json.dumps(response.json(), ensure_ascii=False)
    except Exception as exc:
        return tool_error(f"Cloud Mail connection failed: {exc}")


SCHEMA = {
    "name": "cloud_mail",
    "description": "Manage Hermes Cloud Mail: list, read, search, send, reply, download attachments, move, or delete messages.",
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["list", "read", "search", "send", "reply", "attachment", "move", "delete"]},
        "email_id": {"type": "integer"}, "attachment_id": {"type": "integer"},
        "email_ids": {"type": "array", "items": {"type": "integer"}},
        "query": {"type": "string"}, "folder": {"type": "string"},
        "to": {"type": "array", "items": {"type": "string"}}, "subject": {"type": "string"},
        "text": {"type": "string"}, "html": {"type": "string"},
        "client_request_id": {"type": "string"}, "thread_id": {"type": "string"},
        "attachments": {"type": "array", "items": {"type": "object"}},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        "offset": {"type": "integer", "minimum": 0},
    }, "required": ["action"]},
}


def cloud_mail_tool(args: Dict[str, Any] | None = None, **_: Any) -> str:
    args = args or {}
    action = args.get("action", "list")
    if action == "list": return _request("list", payload={k: args[k] for k in ("folder", "limit", "offset") if k in args})
    if action == "search": return _request("search", payload={"query": args.get("query", ""), "limit": args.get("limit", 20)})
    if action == "read": return _request(f"read/{int(args['email_id'])}")
    if action == "attachment": return _request(f"attachment/{int(args['email_id'])}/{int(args['attachment_id'])}")
    if action in {"send", "reply"}:
        payload = dict(args); payload.setdefault("client_request_id", str(uuid.uuid4()))
        return _request(action, "POST", payload)
    if action == "move": return _request("move", "POST", {"email_ids": args.get("email_ids", []), "folder": args.get("folder", "archive")})
    if action == "delete": return _request("delete", "POST", {"email_ids": args.get("email_ids", [])})
    return tool_error(f"Unknown Cloud Mail action: {action}")


registry.register(name="cloud_mail", toolset="messaging", schema=SCHEMA, handler=lambda args, **kw: cloud_mail_tool(args, **kw), emoji="✉️", requires_env=["HERMES_CLOUD_MAIL_URL", "HERMES_CLOUD_MAIL_TOKEN"])
