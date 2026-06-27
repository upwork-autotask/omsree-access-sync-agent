"""Agent configuration, loaded from the environment / a .env file.

Mirrors the keys documented in .env.example. Everything the agent needs to run
is captured here so the rest of the code never reads os.environ directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # optional dependency; .env is a convenience, not a requirement
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - exercised only when dotenv is absent
    def load_dotenv(*_args, **_kwargs):  # type: ignore[misc]
        return False


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"expected an integer, got {value!r}")


def _as_list(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class Config:
    access_db_path: Path
    crm_base_url: str
    agent_token: str
    access_db_password: str = ""
    table_whitelist: list[str] = field(default_factory=list)
    sync_interval_minutes: int = 15
    dry_run: bool = True
    state_dir: Path = field(default_factory=Path.cwd)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, *, dotenv_path: str | os.PathLike | None = None) -> "Config":
        """Build a Config from environment variables (loading .env first)."""
        if env is None:
            load_dotenv(dotenv_path) if dotenv_path else load_dotenv()
            env = dict(os.environ)

        access_db_path = env.get("ACCESS_DB_PATH", "").strip()
        crm_base_url = env.get("CRM_BASE_URL", "").strip().rstrip("/")
        agent_token = env.get("AGENT_TOKEN", "").strip()

        missing = [
            name
            for name, value in (
                ("ACCESS_DB_PATH", access_db_path),
                ("CRM_BASE_URL", crm_base_url),
                ("AGENT_TOKEN", agent_token),
            )
            if not value
        ]
        if missing:
            raise ConfigError(
                "missing required config: " + ", ".join(missing) + " (see .env.example)"
            )

        state_dir = env.get("STATE_DIR", "").strip()

        return cls(
            access_db_path=Path(access_db_path),
            crm_base_url=crm_base_url,
            agent_token=agent_token,
            access_db_password=env.get("ACCESS_DB_PASSWORD", ""),
            table_whitelist=_as_list(env.get("TABLE_WHITELIST")),
            sync_interval_minutes=_as_int(env.get("SYNC_INTERVAL_MINUTES"), 15),
            dry_run=_as_bool(env.get("DRY_RUN"), default=True),
            state_dir=Path(state_dir) if state_dir else Path.cwd(),
        )

    @property
    def state_file(self) -> Path:
        return self.state_dir / "last-sync.json"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "sync.log"
