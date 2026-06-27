import pytest

from agent.config import Config, ConfigError


def _base_env(**overrides):
    env = {
        "ACCESS_DB_PATH": r"C:\OmSree\data\omsree.accdb",
        "CRM_BASE_URL": "https://crm.example.com/",
        "AGENT_TOKEN": "tok-123",
    }
    env.update(overrides)
    return env


def test_minimal_config_loads_with_defaults():
    cfg = Config.from_env(_base_env())
    assert cfg.agent_token == "tok-123"
    assert cfg.crm_base_url == "https://crm.example.com"  # trailing slash stripped
    assert cfg.dry_run is True  # safe default
    assert cfg.sync_interval_minutes == 15
    assert cfg.table_whitelist == []


def test_whitelist_and_flags_parse():
    cfg = Config.from_env(
        _base_env(
            TABLE_WHITELIST="unit_master, booking ,payment",
            DRY_RUN="false",
            SYNC_INTERVAL_MINUTES="5",
        )
    )
    assert cfg.table_whitelist == ["unit_master", "booking", "payment"]
    assert cfg.dry_run is False
    assert cfg.sync_interval_minutes == 5


def test_missing_required_keys_raise():
    with pytest.raises(ConfigError) as exc:
        Config.from_env({"ACCESS_DB_PATH": "x"})
    assert "CRM_BASE_URL" in str(exc.value)
    assert "AGENT_TOKEN" in str(exc.value)


def test_bad_interval_raises():
    with pytest.raises(ConfigError):
        Config.from_env(_base_env(SYNC_INTERVAL_MINUTES="soon"))


def test_state_and_log_paths():
    cfg = Config.from_env(_base_env(STATE_DIR=r"C:\OmSree\state"))
    assert cfg.state_file.name == "last-sync.json"
    assert cfg.log_file.name == "sync.log"
