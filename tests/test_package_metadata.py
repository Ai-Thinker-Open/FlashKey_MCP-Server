"""Package metadata consistency tests."""

from __future__ import annotations

import importlib.metadata
import pathlib
import tomllib

import flashkey_mcp


def test_runtime_version_matches_project_metadata() -> None:
    project_root = pathlib.Path(__file__).resolve().parents[1]
    project = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))
    declared_version = project["project"]["version"]

    assert flashkey_mcp.__version__ == declared_version
    assert importlib.metadata.version("flashkey-mcp") == declared_version
