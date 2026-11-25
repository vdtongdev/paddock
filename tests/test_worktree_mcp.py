"""Subprocess stdio and dispatch tests for core/git/worktree_mcp_server.py."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from typing import Any

import pytest

import paddock
from core.git import worktree_mcp_server

REPO_ROOT = Path(__file__).resolve().parent.parent
MCP_SERVER = REPO_ROOT / "core" / "git" / "worktree_mcp_server.py"
WORKTREE_MODULE = REPO_ROOT / "core" / "git" / "worktree.py"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", branch)
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("# Test Repo\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-m", "initial commit")
    return path


class StdioMCPClient:
    """Line-delimited binary UTF-8 LF JSON-RPC 2.0 client over subprocess stdio."""

    def __init__(self, proc_cwd: Path, server_path: Path = MCP_SERVER) -> None:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, str(server_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(proc_cwd),
            env=env,
        )
        assert self.proc.stdin and self.proc.stdout and self.proc.stderr
        self._stderr_chunks: list[bytes] = []
        self._stderr_lock = threading.Lock()
        self._stderr_thread = threading.Thread(
            target=self._drain_stderr,
            name="stdio-mcp-stderr-drain",
            daemon=True,
        )
        self._stderr_thread.start()
        self._next_id = 1
        self._closed = False

    def _drain_stderr(self) -> None:
        stderr = self.proc.stderr
        if stderr is None:
            return
        read_fn = getattr(stderr, "read1", stderr.read)
        try:
            while True:
                chunk = read_fn(4096)
                if not chunk:
                    break
                with self._stderr_lock:
                    self._stderr_chunks.append(chunk)
        except Exception:
            pass

    def _get_stderr(self) -> str:
        with self._stderr_lock:
            return b"".join(self._stderr_chunks).decode("utf-8", errors="replace")

    def _terminate_and_reap(self) -> int:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                return self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=5)
        return self.proc.returncode if self.proc.returncode is not None else self.proc.wait(timeout=5)

    def send_raw(self, raw: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(raw)
        self.proc.stdin.flush()

    def send(self, payload: dict[str, Any], *, newline: bytes = b"\n") -> None:
        frame = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8") + newline
        self.send_raw(frame)

    def read_frame(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = self.proc.stdout.readline()
        assert line, "Unexpected EOF from worktree_mcp_server"
        assert b"\r" not in line, f"CRLF detected in stdout frame: {line!r}"
        assert line.endswith(b"\n"), f"Missing LF on frame: {line!r}"
        return json.loads(line.decode("utf-8"))

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        newline: bytes = b"\n",
    ) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1
        self.send(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
            newline=newline,
        )
        resp = self.read_frame()
        assert resp["id"] == req_id
        return resp

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        result = resp["result"]
        text = result["content"][0]["text"]
        return bool(result["isError"]), json.loads(text)

    def close(self, *, timeout: float = 10.0) -> tuple[int, str]:
        if self._closed:
            return (
                self.proc.returncode if self.proc.returncode is not None else -1,
                self._get_stderr(),
            )

        self._closed = True
        in_exception = sys.exception() is not None
        trailing = b""
        rc: int | None = None
        timed_out: subprocess.TimeoutExpired | None = None

        try:
            if self.proc.stdin is not None:
                try:
                    self.proc.stdin.close()
                except OSError:
                    pass

            try:
                rc = self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                timed_out = exc
                rc = self._terminate_and_reap()

            if timed_out is None and not in_exception and self.proc.stdout is not None:
                try:
                    trailing = self.proc.stdout.read()
                except OSError:
                    trailing = b""
        finally:
            if self.proc.poll() is None:
                rc = self._terminate_and_reap()

            for stream in (self.proc.stdin, self.proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

            self._stderr_thread.join(timeout=5)

            if not self._stderr_thread.is_alive() and self.proc.stderr is not None:
                try:
                    self.proc.stderr.close()
                except OSError:
                    pass

        if not in_exception:
            if timed_out is not None:
                raise timed_out
            assert trailing == b"", f"Unexpected trailing bytes: {trailing!r}"

        assert rc is not None
        return rc, self._get_stderr()


def test_server_metadata_and_version_parity() -> None:
    assert worktree_mcp_server.SERVER_INFO["name"] == "paddock-worktree"
    assert worktree_mcp_server.SERVER_INFO["version"] == paddock.__version__
    assert worktree_mcp_server.DEFAULT_PROTOCOL_VERSION == "2025-03-26"
    assert worktree_mcp_server.SUPPORTED_PROTOCOL_VERSIONS == {
        "2024-11-05",
        "2025-03-26",
        "2025-06-18",
        "2025-11-25",
    }


def test_worktree_mcp_stdio_lifecycle_and_errors(tmp_path: Path) -> None:
    unrelated_cwd = tmp_path / "plugin-dir"
    unrelated_cwd.mkdir()
    target_repo = _init_repo(tmp_path / "target-repo")

    (target_repo / "docs" / "specs").mkdir(parents=True)
    (target_repo / "docs" / "plans").mkdir(parents=True)
    (target_repo / "extra").mkdir(parents=True)
    (target_repo / "docs" / "specs" / "demo-feat.md").write_text("# Spec\n", encoding="utf-8")
    (target_repo / "docs" / "plans" / "demo-feat.md").write_text("# Plan\n", encoding="utf-8")
    (target_repo / "extra" / "notes.txt").write_text("notes\n", encoding="utf-8")

    client = StdioMCPClient(proc_cwd=unrelated_cwd)
    try:
        # 1. server/discover -> -32601 without crashing
        disc = client.request("server/discover")
        assert disc["error"]["code"] == -32601

        # 2. initialize handshake (2025-11-25) + silent notification
        init = client.request(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        assert init["result"]["protocolVersion"] == "2025-11-25"
        assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
        assert init["result"]["serverInfo"]["name"] == "paddock-worktree"
        assert init["result"]["serverInfo"]["version"] == paddock.__version__

        # Unsupported version falls back to DEFAULT_PROTOCOL_VERSION
        init_fallback = client.request(
            "initialize",
            {"protocolVersion": "1999-01-01", "capabilities": {}},
        )
        assert init_fallback["result"]["protocolVersion"] == "2025-03-26"

        # Silent notification emits zero bytes
        client.send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # 3. ping proves notification emitted zero stray output
        ping = client.request("ping")
        assert ping["result"] == {}

        # 4. tools/list returns strictly 3 tools and complete schemas
        listed = client.request("tools/list")
        tools = listed["result"]["tools"]
        assert [t["name"] for t in tools] == [
            "worktree_create",
            "worktree_list",
            "worktree_remove",
        ]
        by_name = {t["name"]: t["inputSchema"] for t in tools}

        create_schema = by_name["worktree_create"]
        assert create_schema["required"] == ["cwd", "slug"]
        assert set(create_schema["properties"].keys()) == {
            "cwd",
            "slug",
            "prefix",
            "base",
            "plan_path",
            "copy_paths",
            "init_codegraph",
        }
        assert create_schema["properties"]["prefix"]["enum"] == [
            "feat",
            "fix",
            "docs",
            "refactor",
            "chore",
            "test",
        ]
        assert create_schema["properties"]["prefix"]["default"] == "feat"
        assert create_schema["properties"]["copy_paths"]["type"] == "array"
        assert create_schema["properties"]["copy_paths"]["items"] == {"type": "string"}
        assert create_schema["properties"]["init_codegraph"]["type"] == "boolean"
        assert create_schema["properties"]["init_codegraph"]["default"] is True

        list_schema = by_name["worktree_list"]
        assert list_schema["required"] == ["cwd"]
        assert set(list_schema["properties"].keys()) == {"cwd"}

        remove_schema = by_name["worktree_remove"]
        assert remove_schema["required"] == ["cwd", "slug"]
        assert set(remove_schema["properties"].keys()) == {"cwd", "slug", "delete_branch"}
        assert remove_schema["properties"]["delete_branch"]["type"] == "boolean"
        assert remove_schema["properties"]["delete_branch"]["default"] is False

        # 5. JSON-RPC protocol error handling (-32700, -32600, -32601, -32602)
        client.send_raw(b"{not valid json\n")
        parse_err = client.read_frame()
        assert parse_err["id"] is None
        assert parse_err["error"]["code"] == -32700

        client.send_raw(b"[1,2,3]\n")
        non_dict_err = client.read_frame()
        assert non_dict_err["id"] is None
        assert non_dict_err["error"]["code"] == -32600

        client.send({"jsonrpc": "2.0", "id": 9001})
        missing_method_err = client.read_frame()
        assert missing_method_err["id"] == 9001
        assert missing_method_err["error"]["code"] == -32600

        unknown_method = client.request("nonexistent/method")
        assert unknown_method["error"]["code"] == -32601

        for bad_call_params in (
            {},
            {"name": "worktree_list"},
            {"name": "worktree_list", "arguments": "not-a-dict"},
            {"name": 123, "arguments": {}},
            {"name": "   ", "arguments": {}},
        ):
            bad_params_resp = client.request("tools/call", bad_call_params)
            assert bad_params_resp["error"]["code"] == -32602

        is_err, unknown_tool_payload = client.call_tool("unknown_tool", {"cwd": str(target_repo)})
        assert is_err is True and "error" in unknown_tool_payload

        # 6. Strict runtime validation and security rejections (isError: True)
        for bad_cwd in ("", "   ", 123, str(unrelated_cwd)):
            is_err, payload = client.call_tool("worktree_list", {"cwd": bad_cwd})
            assert is_err is True and "error" in payload

        for bad_slug in ("", "   ", 123, "../evil", "a/b", "a..b", "NotKebab"):
            is_err, payload = client.call_tool(
                "worktree_create",
                {"cwd": str(target_repo), "slug": bad_slug, "init_codegraph": False},
            )
            assert is_err is True and "error" in payload

        for bad_plan in ("../secret.md", ".git/plan.md", "", 123):
            is_err, payload = client.call_tool(
                "worktree_create",
                {
                    "cwd": str(target_repo),
                    "slug": "demo-feat",
                    "plan_path": bad_plan,
                    "init_codegraph": False,
                },
            )
            assert is_err is True and "error" in payload

        for bad_copy in ("extra/notes.txt", 123, [123], [""], ["  "], ["../secret.md"], [".git/config"]):
            is_err, payload = client.call_tool(
                "worktree_create",
                {
                    "cwd": str(target_repo),
                    "slug": "demo-feat",
                    "copy_paths": bad_copy,
                    "init_codegraph": False,
                },
            )
            assert is_err is True and "error" in payload

        for bad_bool in ("true", "false", 1, 0, None, []):
            is_err, payload = client.call_tool(
                "worktree_create",
                {"cwd": str(target_repo), "slug": "demo-feat", "init_codegraph": bad_bool},
            )
            assert is_err is True and "error" in payload

            is_err, payload = client.call_tool(
                "worktree_remove",
                {"cwd": str(target_repo), "slug": "demo-feat", "delete_branch": bad_bool},
            )
            assert is_err is True and "error" in payload

        for bad_prefix in ("", "   ", None, 123, "unknown"):
            is_err, payload = client.call_tool(
                "worktree_create",
                {
                    "cwd": str(target_repo),
                    "slug": "demo-feat",
                    "prefix": bad_prefix,
                    "init_codegraph": False,
                },
            )
            assert is_err is True and "error" in payload

        for bad_base in ("", "   ", 123, "-main", "a..b"):
            is_err, payload = client.call_tool(
                "worktree_create",
                {
                    "cwd": str(target_repo),
                    "slug": "demo-feat",
                    "base": bad_base,
                    "init_codegraph": False,
                },
            )
            assert is_err is True and "error" in payload

        assert not (target_repo / ".worktrees").exists()

        # 7. worktree_create routes by explicit cwd (NOT unrelated_cwd) and copies plan + extra
        is_err, created = client.call_tool(
            "worktree_create",
            {
                "cwd": str(target_repo),
                "slug": "demo-feat",
                "plan_path": "docs/plans/demo-feat.md",
                "copy_paths": ["extra/notes.txt"],
                "init_codegraph": False,
            },
        )
        assert is_err is False
        wt_path = Path(created["worktree_path"])
        assert wt_path == (target_repo / ".worktrees" / "demo-feat").resolve()
        assert created["branch"] == "feat/demo-feat"
        assert created["base"] == "main"
        assert created["created"] is True
        assert created["reused"] is False
        assert created["reused_branch"] is False
        assert created["exclude_updated"] is True
        assert created["copied_files"] == [
            "docs/specs/demo-feat.md",
            "docs/plans/demo-feat.md",
            "extra/notes.txt",
        ]
        assert created["codegraph_status"] == "skipped_disabled"
        assert (wt_path / "docs" / "plans" / "demo-feat.md").read_text(encoding="utf-8") == "# Plan\n"
        assert (wt_path / "docs" / "specs" / "demo-feat.md").read_text(encoding="utf-8") == "# Spec\n"
        assert (wt_path / "extra" / "notes.txt").read_text(encoding="utf-8") == "notes\n"
        assert ".worktrees/" in (target_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert not (target_repo / ".gitignore").exists()

        # CRLF input line still produces LF-only output frame; idempotent reuse succeeds
        reused_resp = client.request(
            "tools/call",
            {
                "name": "worktree_create",
                "arguments": {"cwd": str(target_repo), "slug": "demo-feat", "init_codegraph": False},
            },
            newline=b"\r\n",
        )
        reused = json.loads(reused_resp["result"]["content"][0]["text"])
        assert reused_resp["result"]["isError"] is False
        assert reused["created"] is False
        assert reused["reused"] is True
        assert reused["exclude_updated"] is False
        assert reused["copied_files"] == []

        # 8. worktree_list returns main + Paddock-managed worktree
        is_err, listed_wts = client.call_tool("worktree_list", {"cwd": str(target_repo)})
        assert is_err is False
        assert listed_wts["repo_root"] == str(target_repo.resolve())
        by_slug = {w["slug"]: w for w in listed_wts["worktrees"]}
        assert set(by_slug.keys()) == {None, "demo-feat"}
        assert by_slug[None]["is_main"] is True
        assert by_slug[None]["is_paddock_managed"] is False
        assert by_slug["demo-feat"]["is_main"] is False
        assert by_slug["demo-feat"]["is_paddock_managed"] is True
        assert by_slug["demo-feat"]["branch"] == "feat/demo-feat"

        # 9. Dirty removal fails non-force with isError: True
        dirty_file = wt_path / "dirty.txt"
        dirty_file.write_text("unsaved work\n", encoding="utf-8")
        is_err, dirty_err = client.call_tool(
            "worktree_remove",
            {"cwd": str(target_repo), "slug": "demo-feat", "delete_branch": True},
        )
        assert is_err is True and "error" in dirty_err
        assert wt_path.exists()

        # 10. Clean worktree_remove with delete_branch=True removes worktree and merged branch
        dirty_file.unlink()
        for rel in created["copied_files"]:
            (wt_path / rel).unlink()
        is_err, removed = client.call_tool(
            "worktree_remove",
            {"cwd": str(target_repo), "slug": "demo-feat", "delete_branch": True},
        )
        assert is_err is False
        assert removed["removed"] is True
        assert removed["slug"] == "demo-feat"
        assert removed["branch"] == "feat/demo-feat"
        assert removed["branch_deleted"] is True
        assert removed["branch_delete_warning"] is None
        assert not wt_path.exists()

        is_err, after_rm = client.call_tool("worktree_list", {"cwd": str(target_repo)})
        assert is_err is False
        assert len(after_rm["worktrees"]) == 1
        assert after_rm["worktrees"][0]["is_main"] is True

        # 11. Default delete_branch=False keeps branch, and default init_codegraph=True skips cleanly
        is_err, created_fix = client.call_tool(
            "worktree_create",
            {"cwd": str(target_repo), "slug": "bug-42", "prefix": "fix", "base": "main"},
        )
        assert is_err is False
        assert created_fix["branch"] == "fix/bug-42"
        assert created_fix["codegraph_status"] == "skipped_no_source_index"

        is_err, removed_keep = client.call_tool(
            "worktree_remove",
            {"cwd": str(target_repo), "slug": "bug-42"},
        )
        assert is_err is False
        assert removed_keep["removed"] is True
        assert removed_keep["branch"] == "fix/bug-42"
        assert removed_keep["branch_deleted"] is False
        branches = _git(target_repo, "branch", "--list", "fix/bug-42").stdout.strip()
        assert "fix/bug-42" in branches
    finally:
        rc, stderr = client.close()

    assert rc == 0
    assert "[paddock-worktree]" in stderr


def test_dispatch_tool_rejects_non_dict_args() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        worktree_mcp_server.dispatch_tool("worktree_list", ["not-a-dict"])  # type: ignore[arg-type]


def test_flat_paddock_core_layout_runs_standalone(tmp_path: Path) -> None:
    """Verify flat platforms/<plat>/scripts/paddock_core layout works identically outside core/."""
    flat_dir = tmp_path / "paddock_core"
    flat_dir.mkdir()
    shutil.copy2(WORKTREE_MODULE, flat_dir / "worktree.py")
    shutil.copy2(MCP_SERVER, flat_dir / "worktree_mcp_server.py")

    proc_cwd = tmp_path / "other-dir"
    proc_cwd.mkdir()
    repo = _init_repo(tmp_path / "repo")

    client = StdioMCPClient(proc_cwd=proc_cwd, server_path=flat_dir / "worktree_mcp_server.py")
    try:
        init = client.request("initialize", {"protocolVersion": "2024-11-05"})
        assert init["result"]["protocolVersion"] == "2024-11-05"
        assert init["result"]["serverInfo"]["name"] == "paddock-worktree"

        is_err, listed = client.call_tool("worktree_list", {"cwd": str(repo)})
        assert is_err is False
        assert listed["repo_root"] == str(repo.resolve())
        assert len(listed["worktrees"]) == 1
    finally:
        rc, _ = client.close()
    assert rc == 0


def test_stdio_client_drains_large_stderr_without_deadlock(tmp_path: Path) -> None:
    """Verify StdioMCPClient drains >4 KB Windows stderr pipes continuously."""
    noisy_server = tmp_path / "noisy_server.py"
    noisy_server.write_text(
        "\n".join(
            [
                "import json, sys",
                "while True:",
                "    line = sys.stdin.buffer.readline()",
                "    if not line:",
                "        break",
                "    msg = json.loads(line.decode('utf-8'))",
                "    for i in range(400):",
                "        sys.stderr.write(f'[noisy] line {i:03d}: ' + ('x' * 100) + '\\n')",
                "    sys.stderr.flush()",
                "    resp = {'jsonrpc': '2.0', 'id': msg['id'], 'result': {'ok': True}}",
                "    sys.stdout.buffer.write(json.dumps(resp).encode('utf-8') + b'\\n')",
                "    sys.stdout.buffer.flush()",
            ]
        ),
        encoding="utf-8",
    )

    client = StdioMCPClient(proc_cwd=tmp_path, server_path=noisy_server)
    try:
        resp = client.request("ping")
        assert resp["result"] == {"ok": True}
    finally:
        rc, stderr = client.close()

    assert rc == 0
    assert len(stderr.encode("utf-8")) > 40_000
    assert "[noisy] line 000:" in stderr
    assert "[noisy] line 399:" in stderr


def test_stdio_client_close_reaps_subprocess_on_mid_test_failure_and_timeout(tmp_path: Path) -> None:
    """Verify close() reaps subprocess cleanly on mid-test AssertionError and exit timeout."""
    # Mid-test failure inside try/finally preserves original AssertionError and reaps child
    client = StdioMCPClient(proc_cwd=tmp_path)
    with pytest.raises(AssertionError, match="mid-test failure"):
        try:
            ping = client.request("ping")
            assert ping["result"] == {}
            client.send({"jsonrpc": "2.0", "id": 999, "method": "ping", "params": {}})
            raise AssertionError("mid-test failure")
        finally:
            client.close()
    assert client.proc.poll() is not None

    # Child ignoring stdin EOF is terminated/reaped before TimeoutExpired propagates
    stubborn_server = tmp_path / "stubborn_server.py"
    stubborn_server.write_text(
        "\n".join(
            [
                "import time",
                "while True:",
                "    time.sleep(0.5)",
            ]
        ),
        encoding="utf-8",
    )
    stubborn = StdioMCPClient(proc_cwd=tmp_path, server_path=stubborn_server)
    with pytest.raises(subprocess.TimeoutExpired):
        stubborn.close(timeout=0.2)
    assert stubborn.proc.poll() is not None
