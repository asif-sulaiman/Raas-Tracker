"""The demo-recipe reset is a one-off cutover for the v7 database.

Its deletion path ran (or runs) before schema v8 ships, against v7; the commit
that added it carries the v7-shaped tests. On a v8 database the only correct
behaviour is to refuse, which is what is pinned here. Keeping the module
available matters: a database that failed to upgrade still needs the reset.
"""

import importlib.util
from pathlib import Path

import pytest

from raas_tracker.demo_reset import DemoResetRefused, reset_demo_recipes


def test_refuses_once_the_database_is_v8(db):
    with pytest.raises(DemoResetRefused, match="schema version"):
        reset_demo_recipes(db, apply=False)
    assert db.execute("SELECT COUNT(*) FROM recipes").fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM audit_logs WHERE action = 'DEMO_DATA_RESET'"
    ).fetchone()[0] == 0


def test_script_refuses_apply_without_a_real_backup_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused/nowhere")
    project = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "reset_demo_recipes_script", project / "scripts" / "reset_demo_recipes.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    code = module.main(["--yes", "--backup-file", str(tmp_path / "missing.dump")])

    assert code == 2
