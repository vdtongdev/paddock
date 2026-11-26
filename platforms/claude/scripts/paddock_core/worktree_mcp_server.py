# /// script
# requires-python = ">=3.12"
# ///
"""Zero-dependency Python stdlib JSON-RPC 2.0 stdio MCP server (`paddock-worktree`)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import worktree  # noqa: E402

SUPPORTED_PROTOCOL_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}
DEFAULT_PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "paddock-worktree", "version": "0.3.1"}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "worktree_create",
        "description": (
            "Idempotently create or reuse a git worktree at .worktrees/<slug> on <prefix>/<slug>, "
            "ensure .worktrees/ is in .git/info/exclude, copy matching untracked specs/plans, "
            "and optionally initialize CodeGraph."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "slug": {
                    "type": "string",
                    "description": (
                        "Worktree slug matching ^[a-z0-9][a-z0-9._-]{0,63}$ "
                        "(directory name under .worktrees/)."
                    ),
                },
                "prefix": {
                    "type": "string",
                    "enum": list(worktree.VALID_PREFIXES),
                    "default": "feat",
                    "description": "Branch prefix (<prefix>/<slug>).",
                },
                "base": {
                    "type": "string",
                    "description": "Optional base git ref (defaults to main, master, or HEAD).",
                },
                "plan_path": {
                    "type": "string",
                    "description": (
                        "Optional plan file path inside the repository to copy if untracked or missing."
                    ),
                },
                "copy_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional repo-relative file paths to copy if untracked or missing.",
                },
                "init_codegraph": {
                    "type": "boolean",
                    "default": True,
                    "description": "When true, run non-fatal `codegraph init -y` if source .codegraph exists.",
                },
            },
            "required": ["cwd", "slug"],
        },
    },
    {
        "name": "worktree_list",
        "description": (
            "List all registered git worktrees for the repository containing cwd, "
            "identifying main and Paddock-managed .worktrees/<slug> checkouts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
            },
            "required": ["cwd"],
        },
    },
    {
        "name": "worktree_remove",
        "description": (
            "Safely remove a registered Paddock-managed .worktrees/<slug> checkout (non-force only) "
            "and optionally delete its merged branch via git branch -d."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string", "description": "Target workspace/repository path."},
                "slug": {
                    "type": "string",
                    "description": "Paddock-managed worktree slug under .worktrees/ to remove.",
                },
                "delete_branch": {
                    "type": "boolean",
                    "default": False,
                    "description": (
                        "When true, attempt non-force `git branch -d` on the worktree branch "
                        "(never -D, never main or master)."
                    ),
                },
            },
            "required": ["cwd", "slug"],
        },
    },
]


def _log(msg: str) -> None:
    try:
        sys.stderr.write(f"[paddock-worktree] {msg}\n")
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


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Optional argument {key!r} must be a non-empty string")
    return val


def _optional_bool(args: dict[str, Any], key: str, *, default: bool) -> bool:
    if key not in args:
        return default
    val = args[key]
    if not isinstance(val, bool):
        raise ValueError(f"Argument {key!r} must be a boolean")
    return val


def _optional_str_list(args: dict[str, Any], key: str) -> list[str] | None:
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, list) or not all(isinstance(item, str) and item.strip() for item in val):
        raise ValueError(f"Argument {key!r} must be an array of non-empty strings")
    return val


def dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute one MCP tool call strictly against explicit `cwd`."""
    if not isinstance(args, dict):
        raise ValueError("Tool arguments must be a JSON object")

    cwd = _require_str(args, "cwd")

    if name == "worktree_create":
        slug = _require_str(args, "slug")
        prefix = _require_str(args, "prefix") if "prefix" in args else "feat"
        base = _optional_str(args, "base")
        plan_path = _optional_str(args, "plan_path")
        copy_paths = _optional_str_list(args, "copy_paths")
        init_codegraph = _optional_bool(args, "init_codegraph", default=True)
        return worktree.create_worktree(
            cwd,
            slug,
            prefix=prefix,
            base=base,
            plan_path=plan_path,
            copy_paths=copy_paths,
            init_codegraph=init_codegraph,
        )

    if name == "worktree_list":
        return worktree.list_worktrees(cwd)

    if name == "worktree_remove":
        slug = _require_str(args, "slug")
        delete_branch = _optional_bool(args, "delete_branch", default=False)
        return worktree.remove_worktree(cwd, slug, delete_branch=delete_branch)

    raise KeyError(f"Unknown tool: {name}")


def _tool_result(msg_id: Any, payload: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "result": {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "isError": is_error,
        },
    }


def handle_message(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Process one JSON-RPC 2.0 request/notification dict and return response dict or None."""
    if "id" not in msg:
        return None

    msg_id = msg.get("id")
    method = msg.get("method")
    if ("jsonrpc" in msg and msg["jsonrpc"] != "2.0") or not isinstance(method, str) or not method:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32600, "message": "Invalid Request"},
        }

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
        if not isinstance(tool_name, str) or not tool_name.strip() or not isinstance(args, dict):
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32602, "message": "Invalid tools/call params"},
            }
        try:
            result = dispatch_tool(tool_name, args)
            return _tool_result(msg_id, result, is_error=False)
        except KeyError as exc:
            err_msg = str(exc.args[0]) if exc.args else str(exc)
            return _tool_result(msg_id, {"error": err_msg}, is_error=True)
        except Exception as exc:
            _log(f"tool {tool_name} error: {exc}")
            return _tool_result(msg_id, {"error": str(exc)}, is_error=True)

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
