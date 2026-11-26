# /// script
# requires-python = ">=3.12"
# ///
"""Zero-dependency Python stdlib JSON-RPC 2.0 stdio MCP server (`paddock-mem`)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import memory  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "paddock-mem", "version": "0.3.2"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "mem_select",
        "description": (
            "Select matching active memory entries (project layer first, traps first) "
            "with match reasons and full bodies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional file paths to match against entry path globs.",
                },
                "topics": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional topic tags.",
                },
                "type": {
                    "type": "string",
                    "enum": list(memory.VALID_TYPES),
                    "description": "Optional entry type filter.",
                },
                "traps_only": {
                    "type": "boolean",
                    "description": "When true, return only trap entries.",
                },
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "mem_write",
        "description": (
            "Validate frontmatter before writing, atomically write entry inside ~/.paddock/, "
            "refresh MEMORY.md, and return dangling [[link]] warnings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "layer": {"type": "string", "enum": ["user", "project"]},
                "name": {"type": "string", "description": "Kebab-case entry name without .md."},
                "description": {"type": "string", "description": "Single-line summary."},
                "metadata": {
                    "type": "object",
                    "description": "Metadata mapping (type, paths, topics, status, verified, verified_on, stale_after, optional trap/superseded_by).",
                },
                "body": {"type": "string", "description": "Markdown body."},
            },
            "required": ["cwd", "layer", "name", "description", "metadata", "body"],
        },
    },
    {
        "name": "mem_validate",
        "description": "Validate both user and project memory layers and return structured problems.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."}
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "mem_stale",
        "description": "List active memory entries whose stale_after date has passed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "today": {"type": "string", "description": "Optional ISO date override (YYYY-MM-DD)."},
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "mem_deprecate",
        "description": "Mark an entry deprecated, rewrite [[links]] if superseded_by is given, and refresh indexes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "name": {"type": "string", "description": "Entry name to deprecate."},
                "superseded_by": {
                    "type": "string",
                    "description": "Optional replacement entry name.",
                },
            },
            "required": ["cwd", "name"],
        },
    },
    {
        "name": "mem_index",
        "description": "Regenerate both MEMORY.md indexes and return (problems, notices).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."}
            },
            "required": ["cwd"],
        },
    },
]


def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"[paddock-mem] {msg}\n")
        sys.stderr.flush()
    except Exception:
        pass


def _send_frame(obj: dict[str, Any]) -> None:
    data = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _require_str(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Missing or invalid required string argument: {key!r}")
    return val


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one MCP tool call strictly against explicit `cwd`."""
    cwd = _require_str(args, "cwd")

    if name == "mem_select":
        paths = args.get("paths")
        topics = args.get("topics")
        if paths is not None and not isinstance(paths, list):
            raise ValueError("'paths' must be an array of strings")
        if topics is not None and not isinstance(topics, list):
            raise ValueError("'topics' must be an array of strings")
        return memory.select(
            cwd,
            paths=paths,
            topics=topics,
            type_=args.get("type"),
            traps_only=bool(args.get("traps_only", False)),
        )

    if name == "mem_write":
        layer = _require_str(args, "layer")
        entry_name = _require_str(args, "name")
        desc = _require_str(args, "description")
        meta = args.get("metadata")
        if not isinstance(meta, dict):
            raise ValueError("'metadata' must be a JSON object")
        body = args.get("body")
        if not isinstance(body, str):
            raise ValueError("'body' must be a string")
        return memory.write_entry(cwd, layer, entry_name, desc, meta, body)

    if name == "mem_validate":
        return memory.validate(cwd)

    if name == "mem_stale":
        today = args.get("today")
        if today is not None and not isinstance(today, str):
            raise ValueError("'today' must be an ISO date string")
        return memory.stale(cwd, today=today)

    if name == "mem_deprecate":
        entry_name = _require_str(args, "name")
        sup = args.get("superseded_by")
        if sup is not None and not isinstance(sup, str):
            raise ValueError("'superseded_by' must be a string")
        return memory.deprecate(cwd, entry_name, superseded_by=sup)

    if name == "mem_index":
        return memory.index(cwd)

    raise KeyError(f"Unknown tool: {name}")


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process one JSON-RPC 2.0 request/notification dict and return response dict or None."""
    if "id" not in msg:
        return None

    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}

    if method == "initialize":
        req_ver = params.get("protocolVersion")
        ver = (
            req_ver
            if isinstance(req_ver, str) and req_ver in SUPPORTED_PROTOCOL_VERSIONS
            else DEFAULT_PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": ver,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOL_DEFINITIONS}}

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments")
        if not isinstance(tool_name, str) or not isinstance(args, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Invalid tools/call params"},
            }
        try:
            result = dispatch_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except KeyError as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                },
            }
        except Exception as exc:
            _log(f"tool {tool_name} error: {exc}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve_stdio() -> int:
    """Run newline-delimited JSON-RPC 2.0 loop over stdin/stdout binary streams."""
    while True:
        raw_line = sys.stdin.buffer.readline()
        if not raw_line:
            return 0
        text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not text.strip():
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError as exc:
            _log(f"parse error: {exc}")
            _send_frame(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": f"Parse error: {exc}"},
                }
            )
            continue
        if not isinstance(msg, dict):
            _send_frame(
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32600, "message": "Invalid Request"},
                }
            )
            continue
        resp = handle_message(msg)
        if resp is not None:
            _send_frame(resp)


if __name__ == "__main__":
    raise SystemExit(serve_stdio())
