

def test_config_loads_from_env(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "test-token-123")
    monkeypatch.setenv("IDLE_TIMEOUT", "120")
    monkeypatch.setenv("MUSIC_DIR", "/tmp/music")
    import importlib
    import config
    importlib.reload(config)
    assert config.BOT_TOKEN == "test-token-123"
    assert config.IDLE_TIMEOUT == 120
    assert config.MUSIC_DIR == "/tmp/music"


def test_config_defaults(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "tok")
    monkeypatch.delenv("IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("MUSIC_DIR", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    import importlib
    import config
    importlib.reload(config)
    assert config.IDLE_TIMEOUT == 300
    assert config.MUSIC_DIR is None
