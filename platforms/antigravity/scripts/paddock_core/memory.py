# /// script
# requires-python = ">=3.12"
# ///
"""Paddock unified typed-memory engine, validator, indexer, importer, and CLI."""

from __future__ import annotations

import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any

KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
WIKILINK_RE = re.compile(r"\[\[([a-z0-9]+(?:-[a-z0-9]+)*)\]\]")

VALID_TYPES = ("user", "feedback", "project", "reference")
LAYER_TYPES: dict[str, set[str]] = {
    "user": {"user", "feedback", "reference"},
    "project": {"project", "reference"},
}
VALID_STATUS = ("active", "deprecated")
VALID_VERIFIED = ("human", "model")
GENERATED_MARKER = "<!-- paddock:generated-index -->"

TRAP_SECTIONS = ("**Symptom:**", "**Cause:**", "**Do instead:**")
REQUIRED_METADATA_ORDER = (
    "type",
    "trap",
    "paths",
    "topics",
    "status",
    "superseded_by",
    "verified",
    "verified_on",
    "stale_after",
)


def paddock_home() -> Path:
    """Return Paddock home directory (`PADDOCK_HOME` or `~/.paddock`)."""
    override = os.environ.get("PADDOCK_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".paddock").resolve()


def resolve_repo_root(cwd: str | Path) -> Path:
    """Resolve main checkout root via git-common-dir, or resolved path if non-git."""
    cwd_path = Path(cwd).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd_path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common = Path(proc.stdout.strip())
            if common.name == ".git":
                return common.parent.resolve()
            return common.resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd_path


def repo_slug(cwd: str | Path) -> str:
    """Compute canonical repo slug shared across git worktrees."""
    root = resolve_repo_root(cwd)
    normalized = str(root).replace("\\", "/")
    slug = normalized.replace(":", "-").replace("/", "-")
    slug = re.sub(r"[^A-Za-z0-9._-]", "-", slug).strip("-")
    return slug or "root"


def layer_dir(cwd: str | Path, layer: str) -> Path:
    """Return memory directory for `user` or `project` layer."""
    if layer == "user":
        return paddock_home() / "memory"
    if layer == "project":
        return paddock_home() / "projects" / repo_slug(cwd) / "memory"
    raise ValueError(f"Invalid memory layer: {layer!r} (expected 'user' or 'project')")


def validate_name(name: str) -> str:
    """Ensure memory entry name is strictly kebab-case without path separators."""
    if not isinstance(name, str) or not name:
        raise ValueError("Entry name must be a non-empty string")
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Path traversal or separator rejected in entry name: {name!r}")
    if not KEBAB_RE.fullmatch(name):
        raise ValueError(f"Entry name must be kebab-case ([a-z0-9-]+): {name!r}")
    return name


def safe_entry_path(mem_dir: Path, name: str) -> Path:
    """Resolve `<mem_dir>/<name>.md` and verify strict containment inside `mem_dir`."""
    clean = validate_name(name)
    resolved_dir = mem_dir.resolve()
    target = (resolved_dir / f"{clean}.md").resolve()
    if target.parent != resolved_dir:
        raise ValueError(f"Resolved path escapes memory directory: {target}")
    return target


def atomic_write_text(path: Path, text: str) -> None:
    """Atomically write UTF-8 LF text using closed temp file + os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(normalized)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def _strip_inline_comment(raw: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, ch in enumerate(raw):
        if escaped:
            escaped = False
            continue
        if ch == "\\" and in_double:
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double:
            if idx == 0 or raw[idx - 1].isspace():
                return raw[:idx].rstrip()
    return raw.rstrip()


def _parse_scalar(raw: str) -> Any:
    val = _strip_inline_comment(raw).strip()
    if not val:
        return ""
    if (val.startswith('"') and val.endswith('"')) or (
        val.startswith("'") and val.endswith("'")
    ):
        inner = val[1:-1]
        if val[0] == '"':
            inner = (
                inner.replace(r"\\", "\\")
                .replace(r"\"", '"')
                .replace(r"\n", "\n")
                .replace(r"\t", "\t")
            )
        else:
            inner = inner.replace("''", "'")
        return inner
    if val == "true":
        return True
    if val == "false":
        return False
    if val in ("null", "~"):
        return None
    return val


def _parse_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    cur: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    for ch in inner:
        if escaped:
            cur.append(ch)
            escaped = False
            continue
        if ch == "\\" and in_double:
            cur.append(ch)
            escaped = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            cur.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            cur.append(ch)
        elif ch == "," and not in_single and not in_double:
            token = _parse_scalar("".join(cur))
            items.append("" if token is None else str(token))
            cur = []
        else:
            cur.append(ch)
    if in_single or in_double:
        raise ValueError(f"Unterminated quote in inline YAML list: {raw!r}")
    last = _parse_scalar("".join(cur))
    items.append("" if last is None else str(last))
    return items


def parse_entry_text(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter (with nested 2-space `metadata:`) and markdown body."""
    normalized = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValueError("Missing opening YAML frontmatter delimiter '---'")

    end_idx: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx is None:
        raise ValueError("Missing closing YAML frontmatter delimiter '---'")

    fm_lines = lines[1:end_idx]
    body = "\n".join(lines[end_idx + 1 :])

    top: dict[str, Any] = {}
    meta: dict[str, Any] = {}
    in_metadata = False
    current_list_key: str | None = None

    for raw_line in fm_lines:
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()

        if indent == 0:
            current_list_key = None
            in_metadata = False
            if ":" not in stripped:
                raise ValueError(f"Malformed frontmatter line: {raw_line!r}")
            key, rest = stripped.split(":", 1)
            key = key.strip()
            rest_clean = _strip_inline_comment(rest).strip()
            if key == "metadata":
                if rest_clean:
                    raise ValueError("'metadata:' must introduce a nested mapping")
                in_metadata = True
            else:
                top[key] = _parse_scalar(rest)
            continue

        if not in_metadata:
            raise ValueError(f"Unexpected indentation outside metadata block: {raw_line!r}")

        if stripped.startswith("- "):
            if current_list_key is None:
                raise ValueError(f"List item without preceding key: {raw_line!r}")
            val = _parse_scalar(stripped[2:])
            meta[current_list_key].append("" if val is None else str(val))
            continue

        if ":" not in stripped:
            raise ValueError(f"Malformed metadata line: {raw_line!r}")
        current_list_key = None
        mkey, mrest = stripped.split(":", 1)
        mkey = mkey.strip()
        mrest_clean = _strip_inline_comment(mrest).strip()
        if not mrest_clean:
            meta[mkey] = []
            current_list_key = mkey
        elif mrest_clean.startswith("[") and mrest_clean.endswith("]"):
            meta[mkey] = _parse_inline_list(mrest_clean)
        else:
            meta[mkey] = _parse_scalar(mrest)

    if "metadata" not in top and not meta:
        raise ValueError("Missing required 'metadata:' block")
    top["metadata"] = meta
    return top, body


def _format_scalar(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if val is None:
        return "null"
    s = str(val)
    needs_quote = (
        s == ""
        or s in ("true", "false", "null", "~")
        or s[0].isspace()
        or s[-1].isspace()
        or any(ch in s for ch in (":", "#", "[", "]", "{", "}", ",", '"', "'", "\n"))
    )
    if not needs_quote:
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _format_list(items: list[str]) -> str:
    formatted = [_format_scalar(str(item)) for item in items]
    return "[" + ", ".join(formatted) + "]"


def render_entry(name: str, description: str, metadata: dict[str, Any], body: str) -> str:
    """Serialize frontmatter and body into canonical UTF-8 LF markdown."""
    lines = [
        "---",
        f"name: {_format_scalar(name)}",
        f"description: {_format_scalar(description)}",
        "metadata:",
    ]
    seen: set[str] = set()
    ordered_keys = [k for k in REQUIRED_METADATA_ORDER if k in metadata] + sorted(
        k for k in metadata if k not in REQUIRED_METADATA_ORDER
    )
    for key in ordered_keys:
        if key in seen:
            continue
        seen.add(key)
        val = metadata[key]
        if val is None:
            continue
        if isinstance(val, list):
            lines.append(f"  {key}: {_format_list([str(x) for x in val])}")
        else:
            lines.append(f"  {key}: {_format_scalar(val)}")
    lines.append("---")
    clean_body = body.replace("\r\n", "\n").replace("\r", "\n").lstrip("\n")
    if clean_body and not clean_body.endswith("\n"):
        clean_body += "\n"
    elif not clean_body:
        clean_body = ""
    return "\n".join(lines) + "\n" + clean_body


def _is_valid_iso_date(val: Any) -> bool:
    if not isinstance(val, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", val):
        return False
    try:
        datetime.date.fromisoformat(val)
        return True
    except ValueError:
        return False


def validate_entry_data(
    name: Any,
    description: Any,
    metadata: Any,
    body: str,
    *,
    layer: str | None = None,
    file_stem: str | None = None,
) -> list[str]:
    """Validate parsed entry fields and return list of human-readable problem strings."""
    errors: list[str] = []

    if not isinstance(name, str) or not KEBAB_RE.fullmatch(name):
        errors.append(f"invalid name {name!r}: must be kebab-case [a-z0-9-]+")
    elif file_stem is not None and name != file_stem:
        errors.append(f"name {name!r} does not match filename stem {file_stem!r}")

    if not isinstance(description, str) or not description.strip():
        errors.append("description must be a non-empty string")
    elif "\n" in description or "\r" in description:
        errors.append("description must be single-line")

    if not isinstance(metadata, dict):
        errors.append("metadata must be a mapping")
        return errors

    entry_type = metadata.get("type")
    if entry_type not in VALID_TYPES:
        errors.append(f"invalid metadata.type {entry_type!r}; expected one of {VALID_TYPES}")
    elif layer in LAYER_TYPES and entry_type not in LAYER_TYPES[layer]:
        errors.append(
            f"type {entry_type!r} is not allowed in {layer!r} layer "
            f"(allowed: {sorted(LAYER_TYPES[layer])})"
        )

    trap = metadata.get("trap", False)
    if not isinstance(trap, bool):
        errors.append(f"metadata.trap must be a boolean, got {trap!r}")
    elif trap:
        missing_sections = [s for s in TRAP_SECTIONS if s not in body]
        if missing_sections:
            errors.append(f"trap entry body missing required sections: {', '.join(missing_sections)}")

    for list_key in ("paths", "topics"):
        if list_key in metadata:
            raw_list = metadata[list_key]
            if not isinstance(raw_list, list) or not all(
                isinstance(x, str) and x.strip() for x in raw_list
            ):
                errors.append(f"metadata.{list_key} must be a list of non-empty strings")

    status = metadata.get("status")
    if status not in VALID_STATUS:
        errors.append(f"invalid metadata.status {status!r}; expected one of {VALID_STATUS}")

    superseded_by = metadata.get("superseded_by")
    if superseded_by is not None and superseded_by != "":
        if not isinstance(superseded_by, str) or not KEBAB_RE.fullmatch(superseded_by):
            errors.append(f"invalid metadata.superseded_by {superseded_by!r}: must be kebab-case")

    verified = metadata.get("verified")
    if verified not in VALID_VERIFIED:
        errors.append(f"invalid metadata.verified {verified!r}; expected one of {VALID_VERIFIED}")

    if not _is_valid_iso_date(metadata.get("verified_on")):
        errors.append(
            f"invalid metadata.verified_on {metadata.get('verified_on')!r}; expected YYYY-MM-DD"
        )

    stale_after = metadata.get("stale_after")
    if entry_type in ("project", "reference"):
        if not isinstance(stale_after, str) or (
            stale_after != "never" and not _is_valid_iso_date(stale_after)
        ):
            errors.append(
                f"metadata.stale_after is required for {entry_type!r} ('YYYY-MM-DD' or 'never'), "
                f"got {stale_after!r}"
            )
    elif stale_after is not None and stale_after != "":
        if not isinstance(stale_after, str) or (
            stale_after != "never" and not _is_valid_iso_date(stale_after)
        ):
            errors.append(
                f"invalid metadata.stale_after {stale_after!r}; expected 'YYYY-MM-DD' or 'never'"
            )

    return errors


def match_glob(rel_path: str, pattern: str) -> bool:
    """Match normalized relative path against glob supporting `**`, `*`, `?`."""
    norm_path = rel_path.replace("\\", "/").lstrip("./")
    norm_pat = pattern.replace("\\", "/").lstrip("./")
    if not norm_pat:
        return False

    regex_parts: list[str] = ["^"]
    i = 0
    n = len(norm_pat)
    while i < n:
        if norm_pat[i : i + 3] == "**/":
            regex_parts.append("(?:.+/)?")
            i += 3
        elif norm_pat[i : i + 3] == "/**" and i + 3 == n:
            regex_parts.append("(?:/.*)?")
            i += 3
        elif norm_pat[i : i + 2] == "**":
            regex_parts.append(".*")
            i += 2
        elif norm_pat[i] == "*":
            regex_parts.append("[^/]*")
            i += 1
        elif norm_pat[i] == "?":
            regex_parts.append("[^/]")
            i += 1
        else:
            regex_parts.append(re.escape(norm_pat[i]))
            i += 1
    regex_parts.append("$")
    compiled = re.compile("".join(regex_parts))
    if compiled.fullmatch(norm_path):
        return True
    if "/" not in norm_pat:
        basename = norm_path.rsplit("/", 1)[-1]
        if compiled.fullmatch(basename):
            return True
    return False


def _scan_layer(mem_dir: Path, layer: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    entries: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    if not mem_dir.is_dir():
        return entries, problems

    for path in sorted(mem_dir.iterdir()):
        if not path.is_file() or path.suffix != ".md" or path.name == "MEMORY.md":
            continue
        if not KEBAB_RE.fullmatch(path.stem):
            problems.append(
                {
                    "file": str(path),
                    "name": path.stem,
                    "error": f"filename {path.name!r} is not kebab-case <name>.md",
                }
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
            parsed, body = parse_entry_text(text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            problems.append({"file": str(path), "name": path.stem, "error": str(exc)})
            continue

        errs = validate_entry_data(
            parsed.get("name"),
            parsed.get("description"),
            parsed.get("metadata"),
            body,
            layer=layer,
            file_stem=path.stem,
        )
        if errs:
            problems.append(
                {"file": str(path), "name": path.stem, "error": "; ".join(errs)}
            )
            continue

        entries.append(
            {
                "layer": layer,
                "file": str(path),
                "name": parsed["name"],
                "description": parsed["description"],
                "metadata": parsed["metadata"],
                "body": body,
            }
        )
    return entries, problems


def _render_memory_index(entries: list[dict[str, Any]], layer: str) -> str:
    active = [e for e in entries if e["metadata"].get("status") == "active"]
    deprecated_count = sum(1 for e in entries if e["metadata"].get("status") == "deprecated")
    traps = [e for e in active if e["metadata"].get("trap")]
    non_traps = [e for e in active if not e["metadata"].get("trap")]

    lines = [GENERATED_MARKER, f"# Paddock Memory Index ({layer})", ""]
    if not active and deprecated_count == 0:
        lines.append("_No memory entries yet._")
        return "\n".join(lines) + "\n"

    if traps:
        lines.append("## Traps")
        lines.append("")
        for entry in sorted(traps, key=lambda e: e["name"]):
            lines.append(f"- [[{entry['name']}]]: {entry['description']}")
        lines.append("")

    by_type: dict[str, list[dict[str, Any]]] = {}
    for entry in non_traps:
        etype = str(entry["metadata"].get("type", "reference"))
        by_type.setdefault(etype, []).append(entry)

    for etype in VALID_TYPES:
        group = by_type.get(etype)
        if not group:
            continue
        lines.append(f"## {etype.capitalize()}")
        lines.append("")
        for entry in sorted(group, key=lambda e: e["name"]):
            lines.append(f"- [[{entry['name']}]]: {entry['description']}")
        lines.append("")

    if deprecated_count:
        lines.append(f"_Deprecated entries: {deprecated_count}_")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def refresh_layer(mem_dir: Path, layer: str) -> tuple[list[dict[str, str]], list[str]]:
    """Regenerate MEMORY.md for one layer, returning (problems, notices) separately."""
    notices: list[str] = []
    if not mem_dir.exists():
        return [], notices

    index_path = mem_dir / "MEMORY.md"
    if index_path.is_file():
        existing = index_path.read_text(encoding="utf-8", errors="replace")
        if GENERATED_MARKER not in existing:
            backup_path = mem_dir / "MEMORY.md.bak"
            atomic_write_text(backup_path, existing)
            notices.append(f"Backed up non-generated {layer} MEMORY.md to {backup_path.name}")

    entries, problems = _scan_layer(mem_dir, layer)
    rendered = _render_memory_index(entries, layer)
    atomic_write_text(index_path, rendered)
    return problems, notices


def format_problems_capped(problems: list[dict[str, str]], limit: int = 3000) -> str:
    """Join problem descriptions within `limit` chars without leading `; ...` on huge first item."""
    if not problems or limit <= 0:
        return ""
    formatted = [f"{Path(p['file']).name}: {p['error']}" for p in problems]

    first = formatted[0]
    if len(first) > limit:
        if limit <= 3:
            return first[:limit]
        return first[: limit - 3] + "..."

    out = first
    for idx in range(1, len(formatted)):
        remaining = len(formatted) - idx
        candidate = f"{out}; {formatted[idx]}"
        suffix = f"; ... {remaining - 1} more" if remaining > 1 else ""
        if len(candidate) + len(suffix) <= limit:
            out = candidate
        else:
            more_suffix = f"; ... {remaining} more"
            if len(out) + len(more_suffix) <= limit:
                out += more_suffix
            break
    return out


def _normalize_query_path(raw_path: str, repo_root: Path) -> str:
    p = Path(raw_path)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(repo_root.resolve())
            return rel.as_posix()
        except ValueError:
            return p.as_posix().lstrip("/")
    return raw_path.replace("\\", "/").lstrip("./")


def _all_active_names(cwd: str | Path) -> set[str]:
    names: set[str] = set()
    for layer in ("project", "user"):
        entries, _ = _scan_layer(layer_dir(cwd, layer), layer)
        for entry in entries:
            if entry["metadata"].get("status") == "active":
                names.add(entry["name"])
    return names


def select(
    cwd: str | Path,
    paths: list[str] | None = None,
    topics: list[str] | None = None,
    type_: str | None = None,
    traps_only: bool = False,
) -> dict[str, Any]:
    """Select active matching memory entries (project layer first, traps first)."""
    repo_root = resolve_repo_root(cwd)
    norm_paths = [_normalize_query_path(p, repo_root) for p in (paths or []) if p]
    norm_topics = {t.strip().lower() for t in (topics or []) if t and t.strip()}
    has_filters = bool(norm_paths or norm_topics)

    selected: list[dict[str, Any]] = []
    for layer in ("project", "user"):
        entries, _ = _scan_layer(layer_dir(cwd, layer), layer)
        layer_matches: list[dict[str, Any]] = []
        for entry in entries:
            meta = entry["metadata"]
            if meta.get("status") != "active":
                continue
            if type_ and meta.get("type") != type_:
                continue
            is_trap = bool(meta.get("trap", False))
            if traps_only and not is_trap:
                continue

            reasons: list[str] = []
            if has_filters:
                for qpath in norm_paths:
                    for pat in meta.get("paths", []):
                        if match_glob(qpath, pat):
                            reasons.append(f"path:{pat}")
                for topic in meta.get("topics", []):
                    if topic.lower() in norm_topics:
                        reasons.append(f"topic:{topic}")
                if not reasons:
                    continue
            else:
                if traps_only:
                    reasons.append("trap")
                elif type_:
                    reasons.append(f"type:{type_}")
                else:
                    reasons.append("all")

            dedup_reasons = list(dict.fromkeys(reasons))
            layer_matches.append(
                {
                    "layer": layer,
                    "name": entry["name"],
                    "description": entry["description"],
                    "metadata": meta,
                    "match_reasons": dedup_reasons,
                    "body": entry["body"],
                }
            )

        layer_matches.sort(key=lambda e: (not bool(e["metadata"].get("trap", False)), e["name"]))
        selected.extend(layer_matches)

    return {
        "repo_slug": repo_slug(cwd),
        "entries": selected,
        "count": len(selected),
    }


def write_entry(
    cwd: str | Path,
    layer: str,
    name: str,
    description: str,
    metadata: dict[str, Any],
    body: str,
) -> dict[str, Any]:
    """Validate before writing atomically inside Paddock home, refresh index, warn on dangling links."""
    mem_dir = layer_dir(cwd, layer)
    target = safe_entry_path(mem_dir, name)
    errs = validate_entry_data(name, description, metadata, body, layer=layer, file_stem=name)
    if errs:
        raise ValueError("; ".join(errs))

    rendered = render_entry(name, description, metadata, body)
    atomic_write_text(target, rendered)

    problems, notices = refresh_layer(mem_dir, layer)
    active_names = _all_active_names(cwd)
    links = WIKILINK_RE.findall(body)
    dangling = sorted({lnk for lnk in links if lnk not in active_names})
    return {
        "written": str(target),
        "layer": layer,
        "name": name,
        "dangling_links": dangling,
        "problems": problems,
        "notices": notices,
    }


def validate(cwd: str | Path) -> dict[str, Any]:
    """Validate both project and user memory layers."""
    all_problems: list[dict[str, str]] = []
    for layer in ("project", "user"):
        _, problems = _scan_layer(layer_dir(cwd, layer), layer)
        all_problems.extend(problems)
    return {"problems": all_problems, "valid": len(all_problems) == 0}


def stale(cwd: str | Path, today: str | None = None) -> dict[str, Any]:
    """List active entries whose `stale_after` date is strictly before `today`."""
    ref_date = today or datetime.date.today().isoformat()
    if not _is_valid_iso_date(ref_date):
        raise ValueError(f"Invalid today date {ref_date!r}; expected YYYY-MM-DD")

    stale_entries: list[dict[str, Any]] = []
    for layer in ("project", "user"):
        entries, _ = _scan_layer(layer_dir(cwd, layer), layer)
        for entry in entries:
            meta = entry["metadata"]
            if meta.get("status") != "active":
                continue
            sa = meta.get("stale_after")
            if isinstance(sa, str) and sa != "never" and _is_valid_iso_date(sa) and sa < ref_date:
                stale_entries.append(
                    {
                        "layer": layer,
                        "name": entry["name"],
                        "description": entry["description"],
                        "stale_after": sa,
                        "file": entry["file"],
                    }
                )
    return {"today": ref_date, "entries": stale_entries, "count": len(stale_entries)}


def deprecate(
    cwd: str | Path,
    name: str,
    superseded_by: str | None = None,
) -> dict[str, Any]:
    """Mark entry deprecated, rewrite `[[links]]`, refresh indexes, check exact target errors."""
    clean_name = validate_name(name)
    if superseded_by is not None:
        superseded_by = validate_name(superseded_by)
        if superseded_by == clean_name:
            raise ValueError("superseded_by cannot equal entry name")

    target: Path | None = None
    target_layer: str | None = None
    for layer in ("project", "user"):
        candidate = safe_entry_path(layer_dir(cwd, layer), clean_name)
        if candidate.is_file():
            target = candidate
            target_layer = layer
            break

    if target is None or target_layer is None:
        raise ValueError(f"Memory entry not found: {clean_name}")

    parsed, body = parse_entry_text(target.read_text(encoding="utf-8"))
    meta = dict(parsed["metadata"])
    meta["status"] = "deprecated"
    if superseded_by:
        meta["superseded_by"] = superseded_by
    atomic_write_text(
        target,
        render_entry(parsed["name"], parsed["description"], meta, body),
    )

    links_rewritten = 0
    if superseded_by:
        pattern = re.compile(rf"\[\[{re.escape(clean_name)}\]\]")
        replacement = f"[[{superseded_by}]]"
        for layer in ("project", "user"):
            mdir = layer_dir(cwd, layer)
            if not mdir.is_dir():
                continue
            for p in sorted(mdir.iterdir()):
                if not p.is_file() or p.suffix != ".md" or p.name == "MEMORY.md":
                    continue
                if p.resolve() == target.resolve():
                    continue
                try:
                    other_parsed, other_body = parse_entry_text(p.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, ValueError):
                    continue
                if other_parsed["metadata"].get("status") != "active":
                    continue
                new_body, count = pattern.subn(replacement, other_body)
                if count > 0:
                    atomic_write_text(
                        p,
                        render_entry(
                            other_parsed["name"],
                            other_parsed["description"],
                            other_parsed["metadata"],
                            new_body,
                        ),
                    )
                    links_rewritten += count

    idx = index(cwd)
    target_resolved = target.resolve()
    target_problems = [
        prob
        for prob in idx["problems"]
        if Path(prob["file"]).resolve() == target_resolved
    ]
    if target_problems:
        raise ValueError(f"Deprecated entry failed validation: {target_problems[0]['error']}")

    return {
        "deprecated": clean_name,
        "layer": target_layer,
        "file": str(target),
        "superseded_by": superseded_by,
        "links_rewritten": links_rewritten,
        "problems": idx["problems"],
        "notices": idx["notices"],
    }


def index(cwd: str | Path) -> dict[str, Any]:
    """Regenerate MEMORY.md indexes for both project and user layers."""
    problems: list[dict[str, str]] = []
    notices: list[str] = []
    for layer in ("project", "user"):
        p, n = refresh_layer(layer_dir(cwd, layer), layer)
        problems.extend(p)
        notices.extend(n)
    return {"problems": problems, "notices": notices}


def _candidate_claude_project_dirs(claude_root: Path, cwd: str | Path) -> list[Path]:
    slug = repo_slug(cwd)
    candidates = [
        claude_root / "projects" / slug / "memory",
        claude_root / "projects" / f"-{slug}" / "memory",
    ]
    root_posix = str(resolve_repo_root(cwd)).replace("\\", "/")
    alt_slug = re.sub(r"[^A-Za-z0-9]", "-", root_posix)
    for alt in (alt_slug, alt_slug.lstrip("-")):
        p = claude_root / "projects" / alt / "memory"
        if p not in candidates:
            candidates.append(p)
    return candidates


def import_claude(
    cwd: str | Path | None = None,
    dry_run: bool = False,
    overwrite: bool = False,
    claude_home: str | Path | None = None,
) -> dict[str, Any]:
    """Read-only copy of Claude memory entries into `~/.paddock/`."""
    claude_root = (
        Path(claude_home).expanduser().resolve()
        if claude_home is not None
        else (Path.home() / ".claude").resolve()
    )
    copied: list[dict[str, str]] = []
    skipped: list[dict[str, str]] = []
    touched_layers: list[tuple[str, Path]] = []

    pairs: list[tuple[str, Path, Path]] = [
        ("user", claude_root / "memory", paddock_home() / "memory")
    ]
    if cwd is not None:
        proj_dest = layer_dir(cwd, "project")
        for cand in _candidate_claude_project_dirs(claude_root, cwd):
            if cand.is_dir():
                pairs.append(("project", cand, proj_dest))
                break

    for layer, src_dir, dst_dir in pairs:
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.iterdir()):
            if not src.is_file() or src.suffix != ".md" or src.name == "MEMORY.md":
                continue
            dst = dst_dir / src.name
            if dst.exists() and not overwrite:
                skipped.append({"layer": layer, "file": src.name, "reason": "exists"})
                continue
            if not dry_run:
                content = src.read_text(encoding="utf-8")
                atomic_write_text(dst, content)
                touched_layers.append((layer, dst_dir))
            copied.append({"layer": layer, "src": str(src), "dst": str(dst)})

    problems: list[dict[str, str]] = []
    notices: list[str] = []
    if not dry_run:
        seen_dirs: set[Path] = set()
        for layer, mdir in touched_layers:
            if mdir in seen_dirs:
                continue
            seen_dirs.add(mdir)
            p, n = refresh_layer(mdir, layer)
            problems.extend(p)
            notices.extend(n)

    return {
        "dry_run": dry_run,
        "copied": copied,
        "skipped": skipped,
        "problems": problems,
        "notices": notices,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paddock unified memory CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_sel = sub.add_parser("select")
    p_sel.add_argument("--cwd", default=".")
    p_sel.add_argument("--path", action="append", default=[])
    p_sel.add_argument("--topic", action="append", default=[])
    p_sel.add_argument("--type", dest="entry_type", default=None)
    p_sel.add_argument("--traps-only", action="store_true")

    p_write = sub.add_parser("write")
    p_write.add_argument("--cwd", default=".")
    p_write.add_argument("--layer", choices=("user", "project"), required=True)
    p_write.add_argument("--name", required=True)
    p_write.add_argument("--description", required=True)
    p_write.add_argument("--metadata", required=True, help="JSON metadata object")
    p_write.add_argument("--body", required=True)

    p_val = sub.add_parser("validate")
    p_val.add_argument("--cwd", default=".")

    p_stale = sub.add_parser("stale")
    p_stale.add_argument("--cwd", default=".")
    p_stale.add_argument("--today", default=None)

    p_dep = sub.add_parser("deprecate")
    p_dep.add_argument("--cwd", default=".")
    p_dep.add_argument("--name", required=True)
    p_dep.add_argument("--superseded-by", default=None)

    p_idx = sub.add_parser("index")
    p_idx.add_argument("--cwd", default=".")

    p_imp = sub.add_parser("import-claude")
    p_imp.add_argument("--dry-run", action="store_true")
    p_imp.add_argument("--overwrite", action="store_true")
    p_imp.add_argument("--cwd", default=None)
    p_imp.add_argument("--claude-home", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "select":
            res = select(
                args.cwd,
                paths=args.path,
                topics=args.topic,
                type_=args.entry_type,
                traps_only=args.traps_only,
            )
        elif args.command == "write":
            meta = json.loads(args.metadata)
            res = write_entry(args.cwd, args.layer, args.name, args.description, meta, args.body)
        elif args.command == "validate":
            res = validate(args.cwd)
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0 if res["valid"] else 1
        elif args.command == "stale":
            res = stale(args.cwd, today=args.today)
        elif args.command == "deprecate":
            res = deprecate(args.cwd, args.name, superseded_by=args.superseded_by)
        elif args.command == "index":
            res = index(args.cwd)
        elif args.command == "import-claude":
            res = import_claude(
                cwd=args.cwd,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
                claude_home=args.claude_home,
            )
        else:
            parser.error(f"Unknown command: {args.command}")
            return 2
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
