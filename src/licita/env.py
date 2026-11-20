"""Secrets from `.env` (gitignored): KEY=VALUE lines, never logged, never in config files."""

from __future__ import annotations

import os
from pathlib import Path


class MissingSecretError(RuntimeError):
    pass


def load_env(path: Path = Path(".env")) -> None:
    """Add `.env` entries to the environment; variables already set (and not empty) win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not os.environ.get(key.strip()):  # an empty shell variable must not hide the file's
            os.environ[key.strip()] = value.strip().strip("\"'")


def require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise MissingSecretError(f"{name} is not set: add it to .env (see .env.example)")
    return value
