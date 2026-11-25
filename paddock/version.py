"""Stdlib SemVer 2.0.0 validation and precedence comparison helpers."""

from __future__ import annotations

import re

SEMVER_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?$")
_SEMVER_PARSE_RE = re.compile(
    r"^v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+(?P<build>[0-9A-Za-z.-]+))?\Z"
)
_DIGITS_RE = re.compile(r"^[0-9]+\Z")


def _parse_prerelease(pre_str: str | None) -> tuple[str | int, ...]:
    if pre_str is None:
        return ()
    parts = pre_str.split(".")
    if any(part == "" for part in parts):
        raise ValueError(f"Invalid empty prerelease identifier in {pre_str!r}")
    return tuple(int(part) if _DIGITS_RE.match(part) else part for part in parts)


def validate_release_tag(tag: str) -> str:
    """Validate a release tag strictly against `^v\\d+\\.\\d+\\.\\d+(?:-[0-9A-Za-z.-]+)?$`."""
    if not isinstance(tag, str) or "\n" in tag or "\r" in tag or not SEMVER_TAG_RE.match(tag):
        raise ValueError(
            f"Invalid release tag {tag!r}; expected vX.Y.Z or vX.Y.Z-prerelease"
        )
    if "-" in tag:
        _, pre_str = tag.split("-", 1)
        _parse_prerelease(pre_str)
    return tag


def parse_semver(raw: str) -> tuple[tuple[int, int, int], tuple[str | int, ...]]:
    """Parse a SemVer string (with optional leading `v`) into `((major, minor, patch), prerelease)`."""
    if not isinstance(raw, str) or "\n" in raw or "\r" in raw:
        raise ValueError(f"Invalid semantic version: {raw!r}")
    match = _SEMVER_PARSE_RE.match(raw)
    if match is None:
        raise ValueError(f"Invalid semantic version: {raw!r}")
    core = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
    )
    prerelease = _parse_prerelease(match.group("pre"))
    return core, prerelease


def compare_versions(a: str, b: str) -> int:
    """Compare two semantic versions following SemVer 2.0.0 precedence (-1, 0, or 1)."""
    core_a, pre_a = parse_semver(a)
    core_b, pre_b = parse_semver(b)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    if pre_a == pre_b:
        return 0
    # A normal version has higher precedence than a pre-release version.
    if not pre_a:
        return 1
    if not pre_b:
        return -1

    for id_a, id_b in zip(pre_a, pre_b):
        if id_a == id_b:
            continue
        if isinstance(id_a, int) and isinstance(id_b, int):
            return -1 if id_a < id_b else 1
        # Numeric identifiers always have lower precedence than non-numeric identifiers.
        if isinstance(id_a, int):
            return -1
        if isinstance(id_b, int):
            return 1
        return -1 if id_a < id_b else 1

    return -1 if len(pre_a) < len(pre_b) else 1


def is_newer_version(candidate: str, current: str) -> bool:
    """Return True if `candidate` has strictly higher SemVer precedence than `current`."""
    return compare_versions(candidate, current) > 0
