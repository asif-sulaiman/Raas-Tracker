"""data_dir / DB_PATH resolution tests (split-safety net)."""
import os

from chemcalc.db import DB_PATH, JSON_PATH, data_dir


def test_data_dir_defaults_to_repo_root():
    assert os.path.dirname(DB_PATH) == data_dir()
    assert os.path.basename(DB_PATH) == "chem_stock.db"
    assert os.path.basename(JSON_PATH) == "stock_data.json"
    # Must NOT resolve inside the package directory.
    assert os.path.basename(data_dir()) != "chemcalc"


def test_data_dir_env_override(tmp_path, monkeypatch):
    target = str(tmp_path / "vol")
    monkeypatch.setenv("CHEMCALC_DATA_DIR", target)
    assert data_dir() == target
    assert os.path.isdir(target)
