"""Subprocess stdio tests for core/memory/mcp_server.py."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = REPO_ROOT / "core" / "memory" / "mcp_server.py"


class StdioMCPClient:
    def __init__(self, proc_cwd: Path, paddock_home: Path) -> None:
        env = os.environ.copy()
        env["PADDOCK_HOME"] = str(paddock_home)
        env["PYTHONUTF8"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, str(MCP_SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(proc_cwd),
            env=env,
        )
        assert self.proc.stdin and self.proc.stdout and self.proc.stderr
        self._next_id = 1

    def send(self, payload: dict) -> None:
        frame = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + b"\n"
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        req_id = self._next_id
        self._next_id += 1
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        line = self.proc.stdout.readline()
        assert line, "Unexpected EOF from mcp_server"
        assert b"\r" not in line, f"CRLF detected in stdout frame: {line!r}"
        assert line.endswith(b"\n"), f"Missing LF on frame: {line!r}"
        resp = json.loads(line.decode("utf-8"))
        assert resp["id"] == req_id
        return resp

    def call_tool(self, name: str, arguments: dict) -> tuple[bool, dict]:
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        result = resp["result"]
        text = result["content"][0]["text"]
        return bool(result["isError"]), json.loads(text)

    def close(self) -> tuple[int, str]:
        self.proc.stdin.close()
        trailing = self.proc.stdout.read()
        assert trailing == b"", f"Unexpected trailing bytes: {trailing!r}"
        rc = self.proc.wait(timeout=10)
        stderr = self.proc.stderr.read().decode("utf-8", errors="replace")
        return rc, stderr


def test_mcp_stdio_all_tools_and_security(tmp_path: Path) -> None:
    unrelated_cwd = tmp_path / "plugin-dir"
    unrelated_cwd.mkdir()
    target_repo = tmp_path / "target-repo"
    target_repo.mkdir()
    paddock_home = tmp_path / "paddock-home"

    client = StdioMCPClient(proc_cwd=unrelated_cwd, paddock_home=paddock_home)
    try:
        # 1. Antigravity / Claude server/discover probe -> clean -32601 without crashing
        disc = client.request("server/discover")
        assert disc["error"]["code"] == -32601

        # 2. Initialize handshake (2025-11-25) + silent notification
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        assert init["result"]["protocolVersion"] == "2025-11-25"
        assert init["result"]["serverInfo"]["name"] == "paddock-mem"
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 3. Ping proves notification emitted zero stray output
        ping = client.request("ping")
        assert ping["result"] == {}

        # 4. List all 6 tools
        listed = client.request("tools/list")
        names = [t["name"] for t in listed["result"]["tools"]]
        assert names == [
            "mem_select",
            "mem_write",
            "mem_validate",
            "mem_stale",
            "mem_deprecate",
            "mem_index",
        ]

        # 5. Path traversal and invalid metadata rejected atomically before write
        for bad in ("../evil", "a/b", "NotKebab"):
            is_err, payload = client.call_tool(
                "mem_write",
                {
                    "cwd": str(target_repo),
                    "layer": "project",
                    "name": bad,
                    "description": "bad",
                    "metadata": {
                        "type": "project",
                        "status": "active",
                        "verified": "human",
                        "verified_on": "2026-11-23",
                        "stale_after": "2027-01-01",
                    },
                    "body": "x",
                },
            )
            assert is_err is True and "error" in payload

        # Invalid metadata (missing stale_after) writes nothing to disk
        is_err, _ = client.call_tool(
            "mem_write",
            {
                "cwd": str(target_repo),
                "layer": "project",
                "name": "invalid-meta",
                "description": "bad",
                "metadata": {"type": "project", "status": "active", "verified": "human", "verified_on": "2026-11-23"},
                "body": "x",
            },
        )
        assert is_err is True
        assert not paddock_home.exists()

        # 6. mem_write (routes by explicit cwd, NOT unrelated_cwd)
        is_err, w_old = client.call_tool(
            "mem_write",
            {
                "cwd": str(target_repo),
                "layer": "project",
                "name": "old-rule",
                "description": "Old rule",
                "metadata": {
                    "type": "project",
                    "paths": ["src/**"],
                    "topics": ["db"],
                    "status": "active",
                    "verified": "human",
                    "verified_on": "2026-11-23",
                    "stale_after": "2025-01-01",
                },
                "body": "Old detail.\n",
            },
        )
        assert is_err is False
        assert "projects" in w_old["written"]

        is_err, w_new = client.call_tool(
            "mem_write",
            {
                "cwd": str(target_repo),
                "layer": "project",
                "name": "new-rule",
                "description": "New rule",
                "metadata": {
                    "type": "project",
                    "paths": ["src/**"],
                    "topics": ["db"],
                    "status": "active",
                    "verified": "model",
                    "verified_on": "2026-11-23",
                    "stale_after": "2027-11-23",
                },
                "body": "See [[old-rule]].\n",
            },
        )
        assert is_err is False
        assert w_new["dangling_links"] == []

        # 7. mem_select
        is_err, sel = client.call_tool(
            "mem_select",
            {"cwd": str(target_repo), "paths": ["src/app.py"]},
        )
        assert is_err is False
        assert sel["count"] == 2

        # 8. mem_validate
        is_err, val = client.call_tool("mem_validate", {"cwd": str(target_repo)})
        assert is_err is False and val["valid"] is True

        # 9. mem_stale
        is_err, st = client.call_tool("mem_stale", {"cwd": str(target_repo), "today": "2026-11-23"})
        assert is_err is False and [e["name"] for e in st["entries"]] == ["old-rule"]

        # 10. mem_deprecate
        is_err, dep = client.call_tool(
            "mem_deprecate",
            {"cwd": str(target_repo), "name": "old-rule", "superseded_by": "new-rule"},
        )
        assert is_err is False and dep["links_rewritten"] == 1

        # 11. mem_index
        is_err, idx = client.call_tool("mem_index", {"cwd": str(target_repo)})
        assert is_err is False and idx["problems"] == []
    finally:
        rc, _ = client.close()
    assert rc == 0
