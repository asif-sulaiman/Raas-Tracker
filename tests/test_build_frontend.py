"""The build-and-publish step that keeps the Flask-served SPA from going stale.

`react_frontend/` is gitignored and holds the built SPA. The build output is
content-hashed, so publishing has to replace the previous bundle's hashed
assets - a copy that only adds files leaves the shell pointing at bundles that
no longer exist. These tests pin that, and that the rest of the target
directory survives.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from build_frontend import BUILT_ENTRIES, copy_dist  # noqa: E402


def make_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "icons").mkdir()
    (dist / "assets" / "index-abc123.js").write_text("new bundle", encoding="utf-8")
    (dist / "icons" / "icon-192.png").write_bytes(b"icon")
    (dist / "index.html").write_text("<html>new</html>", encoding="utf-8")
    (dist / "manifest.json").write_text("{}", encoding="utf-8")
    return dist


def test_publish_replaces_the_hashed_assets(tmp_path):
    dist = make_dist(tmp_path)
    target = tmp_path / "react_frontend"
    (target / "assets").mkdir(parents=True)
    (target / "assets" / "index-old999.js").write_text("stale bundle", encoding="utf-8")

    published = copy_dist(dist, target)

    assert "assets" in published
    # The stale bundle is gone: the served shell must not reference it.
    assert not (target / "assets" / "index-old999.js").exists()
    assert (target / "assets" / "index-abc123.js").read_text(encoding="utf-8") == "new bundle"


def test_publish_keeps_what_it_did_not_build(tmp_path):
    dist = make_dist(tmp_path)
    target = tmp_path / "react_frontend"
    target.mkdir()
    installer = target / "ChemicalStockTracker.exe"
    installer.write_bytes(b"installer")

    copy_dist(dist, target)

    # The installer lives beside the SPA in the target and is not ours to delete.
    assert installer.exists()
    assert (target / "index.html").read_text(encoding="utf-8") == "<html>new</html>"


def test_publish_is_idempotent(tmp_path):
    dist = make_dist(tmp_path)
    target = tmp_path / "react_frontend"

    copy_dist(dist, target)
    copy_dist(dist, target)

    assert (target / "index.html").exists()
    assert len(list((target / "assets").iterdir())) == 1


def test_publish_refuses_without_a_build(tmp_path):
    with pytest.raises(SystemExit):
        copy_dist(tmp_path / "missing", tmp_path / "react_frontend")


def test_every_built_entry_is_published(tmp_path):
    dist = make_dist(tmp_path)
    target = tmp_path / "react_frontend"

    published = copy_dist(dist, target)

    for name in BUILT_ENTRIES:
        if (dist / name).exists():
            assert name in published
            assert (target / name).exists()
