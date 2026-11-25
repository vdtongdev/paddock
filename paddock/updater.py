"""Release updater for Paddock CLI and synced platform plugins."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any
import urllib.error
import urllib.request

from paddock import __version__, installer, version as version_mod

GITHUB_REPO = "vdtongdev/paddock"
GITHUB_REPO_URL = "https://github.com/vdtongdev/paddock.git"
GITHUB_LATEST_RELEASE_API = "https://api.github.com/repos/vdtongdev/paddock/releases/latest"

_VALID_UPDATE_PLATFORMS: tuple[str, ...] = ("antigravity", "claude", "all")
_WINDOWS_LOCK_MARKERS: tuple[str, ...] = (
    "winerror 5",
    "winerror 32",
    "os error 5",
    "os error 32",
    "being used by another process",
    "access is denied",
)
_BEARER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+|bearer\s+)(\S+)")
_GH_TOKEN_RE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]+\b")


def _github_token() -> str:
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        raw = os.environ.get(env_name, "").strip()
        if raw and "\n" not in raw and "\r" not in raw and "\x00" not in raw:
            return raw
    return ""


def _redact_secrets(text: str) -> str:
    cleaned = _BEARER_RE.sub(r"\1[REDACTED]", text)
    cleaned = _GH_TOKEN_RE.sub("[REDACTED]", cleaned)
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            cleaned = cleaned.replace(raw, "[REDACTED]")
    return cleaned


def _is_windows_lock_error(text: str, exc: BaseException | None = None) -> bool:
    if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (5, 32):
        return True
    lowered = text.lower()
    return any(marker in lowered for marker in _WINDOWS_LOCK_MARKERS)


def _format_urllib_error(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            return f"HTTP {exc.code}: {exc.reason}"
        finally:
            close_fn = getattr(exc, "close", None)
            if callable(close_fn):
                close_fn()
    if isinstance(exc, urllib.error.URLError):
        return f"network error: {exc.reason}"
    return str(exc)


def _read_urlopen_text(req: urllib.request.Request, *, timeout: float = 15.0) -> str:
    opener_res = urllib.request.urlopen(req, timeout=timeout)
    if hasattr(opener_res, "__enter__") and hasattr(opener_res, "__exit__"):
        with opener_res as resp:
            raw = resp.read()
    else:
        try:
            raw = opener_res.read()
        finally:
            close_fn = getattr(opener_res, "close", None)
            if callable(close_fn):
                close_fn()
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    if isinstance(raw, str):
        return raw
    raise ValueError("Unexpected response payload type from GitHub API")


def _extract_release_tag(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("GitHub release payload must be a JSON object")
    raw_tag = payload.get("tag_name")
    if not isinstance(raw_tag, str) or not raw_tag.strip():
        raise ValueError("GitHub release payload missing 'tag_name'")
    return version_mod.validate_release_tag(raw_tag.strip())


def _fetch_latest_via_urllib() -> dict[str, Any]:
    if not GITHUB_LATEST_RELEASE_API.startswith("https://api.github.com/"):
        raise ValueError("Invalid GitHub API URL")
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"paddock/{__version__}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(GITHUB_LATEST_RELEASE_API, headers=headers)
    body = _read_urlopen_text(req, timeout=15.0)
    data = json.loads(body)
    tag = _extract_release_tag(data)
    return {
        "ref": tag,
        "tag": tag,
        "source": "github-api",
        "is_release": True,
    }


def _fetch_latest_via_gh(gh_bin: str) -> dict[str, Any]:
    proc = subprocess.run(
        [gh_bin, "api", f"repos/{GITHUB_REPO}/releases/latest"],
        shell=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if proc.returncode != 0:
        detail = _redact_secrets((proc.stderr or proc.stdout).strip()) or f"exit {proc.returncode}"
        raise RuntimeError(f"gh api exited {proc.returncode}: {detail}")
    data = json.loads(proc.stdout)
    tag = _extract_release_tag(data)
    return {
        "ref": tag,
        "tag": tag,
        "source": "gh-cli",
        "is_release": True,
    }


def resolve_target_ref(*, version: str | None = None, use_main: bool = False) -> dict[str, Any]:
    """Resolve target git ref from `--version`, `--main`, or latest GitHub Release."""
    if version is not None and use_main:
        raise ValueError("Cannot combine --version and --main")

    if version is not None:
        tag = version_mod.validate_release_tag(version)
        return {
            "ref": tag,
            "tag": tag,
            "source": "version",
            "is_release": True,
        }

    if use_main:
        return {
            "ref": "main",
            "tag": None,
            "source": "main",
            "is_release": False,
        }

    try:
        return _fetch_latest_via_urllib()
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        OSError,
        TimeoutError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValueError,
    ) as urllib_exc:
        urllib_err = _redact_secrets(_format_urllib_error(urllib_exc))

    gh_bin = shutil.which("gh")
    if gh_bin is None:
        raise RuntimeError(
            f"Unable to resolve latest release from {GITHUB_LATEST_RELEASE_API} ({urllib_err}), "
            "and `gh` CLI is not installed on PATH. Check network connectivity, set GITHUB_TOKEN/GH_TOKEN, "
            "or pass --version <vX.Y.Z> / --main."
        )

    try:
        return _fetch_latest_via_gh(gh_bin)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError, RuntimeError) as gh_exc:
        gh_err = _redact_secrets(str(gh_exc))
        raise RuntimeError(
            f"Unable to resolve latest release via GitHub API ({urllib_err}) or `gh api` fallback "
            f"({gh_err}). Check network connectivity or pass --version <vX.Y.Z> / --main."
        ) from None


def _build_check_payload(target: dict[str, Any]) -> dict[str, Any]:
    target_tag = target["tag"]
    if target["is_release"] and isinstance(target_tag, str):
        cmp_val = version_mod.compare_versions(target_tag, __version__)
        newer = version_mod.is_newer_version(target_tag, __version__)
        status = "update-available" if newer else ("up-to-date" if cmp_val == 0 else "older-target")
        return {
            "action": "update",
            "mode": "check",
            "status": status,
            "ok": True,
            "exit_code": 0,
            "current_version": __version__,
            "target_ref": target["ref"],
            "target_tag": target_tag,
            "ref_source": target["source"],
            "update_available": newer,
        }
    return {
        "action": "update",
        "mode": "check",
        "status": "branch-target",
        "ok": True,
        "exit_code": 0,
        "current_version": __version__,
        "target_ref": target["ref"],
        "target_tag": None,
        "ref_source": target["source"],
        "update_available": None,
    }


def _emit_payload(payload: dict[str, Any], *, json_output: bool, stream=None) -> None:
    if json_output:
        target_stream = sys.stdout if stream is None else stream
        print(json.dumps(payload, indent=2, ensure_ascii=False), file=target_stream)
        return

    out_stream = sys.stdout if stream is None else stream
    err_stream = sys.stderr if stream is None else stream
    exit_code = int(payload.get("exit_code", 0))
    dest = out_stream if exit_code == 0 else err_stream

    if payload.get("mode") == "check":
        status = payload["status"]
        cur = payload["current_version"]
        ref = payload["target_ref"]
        if status == "update-available":
            print(f"Update available: {cur} -> {ref} (source: {payload['ref_source']})", file=dest)
        elif status == "up-to-date":
            print(f"Paddock is up to date (v{cur}; latest: {ref}).", file=dest)
        elif status == "older-target":
            print(f"Current version v{cur} is newer than target {ref}.", file=dest)
        else:
            print(f"Current version v{cur}; target branch ref: {ref}.", file=dest)
        return

    if payload.get("status") == "locked":
        print(payload["error"], file=err_stream)
        for line in payload["recovery_commands"]:
            print(line, file=err_stream)
        return

    if exit_code != 0:
        print(f"error: {payload.get('error', 'Update failed')}", file=err_stream)
        return

    for line in payload.get("messages", []):
        print(line, file=out_stream)


def _validate_update_options(
    *,
    platform: str | None,
    home_dir: Path | str | None,
) -> Path | None:
    if platform is not None and platform not in _VALID_UPDATE_PLATFORMS:
        raise ValueError(
            f"Unsupported platform {platform!r}; expected 'antigravity', 'claude', or 'all'"
        )
    if home_dir is not None:
        return installer._resolve_home_dir(home_dir)
    return None


def _subprocess_env(paddock_home: Path | str | None) -> dict[str, str] | None:
    if paddock_home is None:
        return None
    env = os.environ.copy()
    env["PADDOCK_HOME"] = str(paddock_home)
    return env


def _run_uv_upgrade(tool_spec: str, ref: str) -> dict[str, Any] | None:
    """Run `uv tool install --force <tool_spec>` and return an error payload on failure, else `None`."""
    uv_bin = shutil.which("uv")
    if uv_bin is None:
        return {
            "action": "update",
            "mode": "full",
            "status": "error",
            "ok": False,
            "exit_code": 1,
            "target_ref": ref,
            "error": (
                "uv is required to upgrade the paddock CLI (not found on PATH). "
                "Install uv (https://docs.astral.sh/uv/) or run `paddock update --plugins-only`."
            ),
        }

    uv_cmd = [uv_bin, "tool", "install", "--force", tool_spec]
    uv_exc: BaseException | None = None
    uv_stdout = ""
    uv_stderr = ""
    uv_rc = 1
    try:
        uv_proc = subprocess.run(
            uv_cmd,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        uv_rc = uv_proc.returncode
        uv_stdout = uv_proc.stdout or ""
        uv_stderr = uv_proc.stderr or ""
    except OSError as exc:
        uv_exc = exc

    if uv_exc is None and uv_rc == 0:
        return None

    combined = _redact_secrets(
        "\n".join(part for part in (uv_stdout, uv_stderr, str(uv_exc or "")) if part)
    )
    if _is_windows_lock_error(combined, uv_exc):
        cmd_upgrade = f"uv tool install --force {tool_spec}"
        cmd_install = "paddock install"
        return {
            "action": "update",
            "mode": "full",
            "status": "locked",
            "ok": False,
            "exit_code": 1,
            "target_ref": ref,
            "error": (
                "Cannot overwrite running paddock executable while it is in use. "
                "Installed plugins were not modified. Run these commands to finish updating:"
            ),
            "recovery_commands": [cmd_upgrade, cmd_install],
            "instructions": f"{cmd_upgrade}\n{cmd_install}",
        }

    detail = _redact_secrets((uv_stderr or uv_stdout or str(uv_exc or "")).strip()) or f"exit {uv_rc}"
    return {
        "action": "update",
        "mode": "full",
        "status": "error",
        "ok": False,
        "exit_code": 1,
        "target_ref": ref,
        "error": f"uv tool install --force {tool_spec} failed: {detail}",
    }


def _run_upgraded_install(
    *,
    target: dict[str, Any],
    platform: str | None,
    effective_home: Path | None,
    home_dir: Path | str | None,
    paddock_home: Path | str | None,
    repo: Path | str | None,
    json_output: bool,
) -> dict[str, Any]:
    """Invoke the newly upgraded `paddock install` binary so plugins install from the new bundle."""
    ref = str(target["ref"])
    paddock_bin = shutil.which("paddock")
    if paddock_bin is None:
        return {
            "action": "update",
            "mode": "full",
            "status": "error",
            "ok": False,
            "exit_code": 1,
            "target_ref": ref,
            "error": (
                f"Upgraded paddock to {ref}, but `paddock` was not found on PATH. "
                "Add uv's tool bin directory to PATH and run: paddock install"
            ),
        }

    install_cmd = [paddock_bin, "install", "--update"]
    if platform is not None:
        install_cmd.extend(["--platform", platform])
    if effective_home is not None and home_dir is not None:
        install_cmd.extend(["--home", str(effective_home)])
    if repo is not None:
        install_cmd.extend(["--repo", str(repo)])
    if json_output:
        install_cmd.append("--json")

    try:
        install_proc = subprocess.run(
            install_cmd,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_subprocess_env(paddock_home),
            timeout=180,
        )
    except OSError as exc:
        return {
            "action": "update",
            "mode": "full",
            "status": "error",
            "ok": False,
            "exit_code": 1,
            "target_ref": ref,
            "error": f"Failed to run upgraded paddock install: {_redact_secrets(str(exc))}",
        }

    if install_proc.returncode != 0:
        detail = _redact_secrets((install_proc.stderr or install_proc.stdout).strip()) or (
            f"exit {install_proc.returncode}"
        )
        return {
            "action": "update",
            "mode": "full",
            "status": "error",
            "ok": False,
            "exit_code": 1,
            "target_ref": ref,
            "error": f"Upgraded paddock install failed: {detail}",
        }

    plugin_report: Any = None
    messages = [f"Upgraded paddock CLI to {ref} (source: {target['source']})"]
    raw_out = (install_proc.stdout or "").strip()
    if json_output and raw_out:
        try:
            plugin_report = json.loads(raw_out)
        except json.JSONDecodeError:
            plugin_report = {"raw_output": raw_out}
    elif raw_out:
        messages.extend(raw_out.splitlines())

    return {
        "action": "update",
        "mode": "full",
        "status": "updated",
        "ok": True,
        "exit_code": 0,
        "current_version": __version__,
        "target_ref": ref,
        "target_tag": target["tag"],
        "ref_source": target["source"],
        "plugin_install": plugin_report,
        "messages": messages,
    }


def run_update(
    *,
    check: bool = False,
    version: str | None = None,
    use_main: bool = False,
    platform: str | None = None,
    plugins_only: bool = False,
    dry_run: bool = False,
    home_dir: Path | str | None = None,
    paddock_home: Path | str | None = None,
    repo: Path | str | None = None,
    json_output: bool = False,
) -> int:
    """Resolve release ref, upgrade Paddock via `uv`, and sync installed platform plugins."""
    try:
        effective_home = _validate_update_options(platform=platform, home_dir=home_dir)
        if check or not plugins_only or version is not None or use_main:
            target = resolve_target_ref(version=version, use_main=use_main)
        else:
            local_tag = f"v{__version__}"
            target = {
                "ref": local_tag,
                "tag": local_tag,
                "source": "local",
                "is_release": True,
            }
    except (ValueError, OSError, RuntimeError) as exc:
        err_msg = _redact_secrets(str(exc))
        if json_output:
            print(json.dumps({"error": err_msg}, indent=2, ensure_ascii=False), file=sys.stderr)
        else:
            print(f"error: {err_msg}", file=sys.stderr)
        return 1

    if check:
        payload = _build_check_payload(target)
        _emit_payload(payload, json_output=json_output)
        return 0

    ref = str(target["ref"])
    tool_spec = f"git+{GITHUB_REPO_URL}@{ref}"

    if dry_run:
        messages: list[str] = []
        if not plugins_only:
            messages.append(f"[dry-run] run: uv tool install --force {tool_spec}")
        install_report = installer.install_targets(
            platform=platform,
            home_dir=effective_home,
            dry_run=True,
            paddock_home=paddock_home,
            repo=repo,
            update_existing=True,
        )
        messages.extend(install_report.get("messages", []))
        payload = {
            "action": "update",
            "mode": "dry-run",
            "status": "dry-run",
            "ok": True,
            "exit_code": 0,
            "dry_run": True,
            "plugins_only": plugins_only,
            "current_version": __version__,
            "target_ref": ref,
            "target_tag": target["tag"],
            "ref_source": target["source"],
            "plugin_install": install_report,
            "messages": messages,
        }
        _emit_payload(payload, json_output=json_output)
        return 0

    if plugins_only:
        try:
            install_report = installer.install_targets(
                platform=platform,
                home_dir=effective_home,
                dry_run=False,
                paddock_home=paddock_home,
                repo=repo,
                update_existing=True,
            )
        except (ValueError, OSError, RuntimeError, FileNotFoundError) as exc:
            payload = {
                "action": "update",
                "mode": "plugins-only",
                "status": "error",
                "ok": False,
                "exit_code": 1,
                "target_ref": ref,
                "error": _redact_secrets(str(exc)),
            }
            _emit_payload(payload, json_output=json_output)
            return 1

        payload = {
            "action": "update",
            "mode": "plugins-only",
            "status": "updated",
            "ok": True,
            "exit_code": 0,
            "plugins_only": True,
            "current_version": __version__,
            "target_ref": ref,
            "target_tag": target["tag"],
            "plugin_install": install_report,
            "messages": install_report.get("messages", []),
        }
        _emit_payload(payload, json_output=json_output)
        return 0

    err_payload = _run_uv_upgrade(tool_spec, ref)
    if err_payload is not None:
        _emit_payload(err_payload, json_output=json_output)
        return 1

    payload = _run_upgraded_install(
        target=target,
        platform=platform,
        effective_home=effective_home,
        home_dir=home_dir,
        paddock_home=paddock_home,
        repo=repo,
        json_output=json_output,
    )
    _emit_payload(payload, json_output=json_output)
    return int(payload["exit_code"])
