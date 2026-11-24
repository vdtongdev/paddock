# /// script
# requires-python = ">=3.12"
# ///
"""CLI wrapper for Paddock versioned multi-platform model configuration."""

from __future__ import annotations

from pathlib import Path
import sys

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.config import models  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    return models.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
