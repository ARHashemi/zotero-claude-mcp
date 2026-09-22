"""Minimal MCP server over stdio — JSON-RPC 2.0, one JSON object per line.

Deliberately dependency-free so it runs on any Python 3.9+ without a virtualenv.
"""

import json
import sys
import traceback

from . import tools

NAME = "zotero"
VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", "2025-06-18"}

INSTRUCTIONS = (
    "Access to the user's Zotero reference library, both the local database on this computer and "
    "their zotero.org account (including group libraries).\n"
    "Typical flow: zotero_search to find references, then zotero_get_item for full metadata, "
    "abstracts and notes, then zotero_attachments to get a PDF path you can open with Read. "
    "zotero_fulltext_search looks inside PDFs and notes. zotero_bibliography formats citations in "
    "any CSL style. Prefer these tools over web search whenever the user refers to their own "
    "library, their papers, their references, their reading, or 'my Zotero'. Writing tools "
    "(zotero_create_item, zotero_add_note) change the user's library — confirm before using them "
    "unless the user asked for the change."
)


def _log(message):
    print(f"[zotero-mcp] {message}", file=sys.stderr, flush=True)


def _result(request_id, payload):
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request):
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if method == "initialize":
        asked = params.get("protocolVersion")
        protocol = asked if asked in SUPPORTED_PROTOCOLS else DEFAULT_PROTOCOL
        return _result(
            request_id,
            {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": NAME, "version": VERSION},
                "instructions": INSTRUCTIONS,
            },
        )

    if method in ("notifications/initialized", "notifications/cancelled", "initialized"):
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": tools.TOOLS})

    if method in ("resources/list", "resources/templates/list"):
        return _result(request_id, {"resources": [], "resourceTemplates": []})

    if method == "prompts/list":
        return _result(request_id, {"prompts": []})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            text = tools.call(name, args)
        except Exception as exc:  # surfaced to the model, not fatal to the server
            _log(f"tool {name} failed: {exc}")
            _log(traceback.format_exc())
            return _result(
                request_id,
                {"content": [{"type": "text", "text": f"Zotero tool error: {exc}"}], "isError": True},
            )
        return _result(request_id, {"content": [{"type": "text", "text": text}], "isError": False})

    if request_id is None:
        return None
    return _error(request_id, -32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"bad JSON on stdin: {exc}")
            continue
        if isinstance(request, list):
            responses = [r for r in (handle(item) for item in request) if r is not None]
            if not responses:
                continue
            payload = responses
        else:
            payload = handle(request)
            if payload is None:
                continue
        stdout.write(json.dumps(payload) + "\n")
        stdout.flush()
