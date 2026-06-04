import os
import stat
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


def test_symlink_is_preserved_not_dereferenced(tmp_path):
    # A symlink under data/ pointing OUTSIDE data/ must be copied as a link,
    # never followed — otherwise it would pull arbitrary files into the backup.
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("SENSITIVE")
    data = tmp_path / "data"; data.mkdir()
    (data / "config.yaml").write_text("server: {}\n")
    (data / "leak").symlink_to(secret)

    dest = snapshot_data_dir(data)

    backed = dest / "leak"
    assert backed.is_symlink()                      # preserved as a link
    assert not backed.read_text() == "SENSITIVE" or backed.is_symlink()
    # the symlink target's content was not materialized as a real file
    assert os.path.islink(backed)


def test_backup_dir_is_owner_only(tmp_path):
    data = tmp_path / "data"; data.mkdir()
    (data / "secrets.db").write_text("x")
    dest = snapshot_data_dir(data)
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    assert mode == 0o700


def test_copied_contents_are_owner_only(tmp_path):
    # The security contract is about the copied secrets, not just the top dir.
    # Seed a world-readable file + subdir so we prove perms are tightened.
    data = tmp_path / "data"; data.mkdir()
    secrets_db = data / "secrets.db"; secrets_db.write_text("KEY")
    os.chmod(secrets_db, 0o644)
    sub = data / "vault"; sub.mkdir(); os.chmod(sub, 0o755)
    nested = sub / "token.json"; nested.write_text("{}"); os.chmod(nested, 0o644)

    dest = snapshot_data_dir(data)

    # No copied file may be group- or world-readable/writable.
    group_world = stat.S_IRWXG | stat.S_IRWXO
    assert stat.S_IMODE(os.stat(dest / "secrets.db").st_mode) & group_world == 0
    assert stat.S_IMODE(os.stat(dest / "vault").st_mode) & group_world == 0
    assert stat.S_IMODE(os.stat(dest / "vault" / "token.json").st_mode) & group_world == 0
