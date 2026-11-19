"""Settings, paths and environment access.

Everything configurable lives in ``config/settings.yaml`` and ``config/styles.yaml``.
Secrets are only ever read from environment variables (GitHub Actions secrets).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def repo_root() -> Path:
    """Repository root (overridable with GTA6_ROOT, used by tests)."""
    env = os.environ.get("GTA6_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[2]


@dataclass
class Config:
    data: dict[str, Any]
    styles: dict[str, Any] = field(default_factory=dict)
    root: Path = field(default_factory=repo_root)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    # Convenience paths -------------------------------------------------
    @property
    def facts_file(self) -> Path:
        return self.path("content", "facts.yaml")

    @property
    def scripts_dir(self) -> Path:
        return self.path("content", "scripts")

    @property
    def data_dir(self) -> Path:
        return self.path("data")

    @property
    def out_dir(self) -> Path:
        return self.path("out")

    @property
    def fonts_dir(self) -> Path:
        return self.path("assets", "fonts")

    @property
    def model_dir(self) -> Path:
        return Path(os.path.expanduser(self.get("voice.model_dir", "~/.cache/gta6shorts/models")))


def load_config(root: Path | None = None) -> Config:
    root = root or repo_root()
    settings_path = root / "config" / "settings.yaml"
    styles_path = root / "config" / "styles.yaml"
    data = yaml.safe_load(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    styles = yaml.safe_load(styles_path.read_text(encoding="utf-8")) if styles_path.exists() else {}
    return Config(data=data or {}, styles=styles or {}, root=root)


def env(name: str, default: str | None = None) -> str | None:
    """Read a secret/config value from the environment; empty strings count as missing."""
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def has_env(*names: str) -> bool:
    return all(env(n) for n in names)
