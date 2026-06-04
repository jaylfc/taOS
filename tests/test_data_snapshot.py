from pathlib import Path
from tinyagentos.data_snapshot import snapshot_data_dir


def test_copies_dbs_and_config_excludes_models_and_workspace(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    (data / "config.yaml").write_text("server: {}\n")
    (data / "chat.db").write_text("sqlite")
    (data / ".auth_user.json").write_text("{}")
    (data / "models").mkdir(); (data / "models" / "big.gguf").write_text("x" * 1000)
    (data / "workspace").mkdir(); (data / "workspace" / "img.png").write_text("y" * 1000)
    (data / "data-backups").mkdir(); (data / "data-backups" / "old").write_text("z")

    dest = snapshot_data_dir(data)

    assert dest.exists()
    assert dest.parent.name == "data-backups"
    assert dest.name.startswith("pre-switch-")
    assert (dest / "config.yaml").read_text() == "server: {}\n"
    assert (dest / "chat.db").exists()
    assert (dest / ".auth_user.json").exists()
    assert not (dest / "models").exists()
    assert not (dest / "workspace").exists()
    assert not (dest / "data-backups").exists()


def test_returns_none_when_data_dir_missing(tmp_path):
    assert snapshot_data_dir(tmp_path / "nope") is None
