"""Tests for Slice D — Cleanup Only (P3)."""

import os
import re
from pathlib import Path


def test_utcnow_replaced():
    """Grep codebase for utcnow — should be 0 occurrences in Python files."""
    root = Path(__file__).parent.parent
    python_files = list(root.rglob("*.py"))
    # Exclude test files and __pycache__
    python_files = [
        f for f in python_files
        if "__pycache__" not in str(f) and not str(f).startswith(str(root / "tests"))
    ]
    occurrences = []
    for py_file in python_files:
        content = py_file.read_text(encoding="utf-8")
        for i, line in enumerate(content.splitlines(), 1):
            if "utcnow" in line:
                # Allow comments mentioning utcnow (e.g., in tests documenting the change)
                if line.strip().startswith("#"):
                    continue
                occurrences.append(f"{py_file.relative_to(root)}:{i}: {line.strip()}")
    assert not occurrences, f"Found utcnow() in Python files:\n" + "\n".join(occurrences)


def test_dockerfile_no_volume_data():
    """Dockerfile doesn't contain VOLUME /data or RAAS_DATA_DIR."""
    dockerfile = Path(__file__).parent.parent / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")
    assert "VOLUME" not in content, "Dockerfile should not contain VOLUME"
    assert "RAAS_DATA_DIR" not in content, "Dockerfile should not contain RAAS_DATA_DIR"


def test_env_example_has_pool_redis():
    """.env.example contains DB_POOL_MAX and REDIS_URL (commented)."""
    env_example = Path(__file__).parent.parent / ".env.example"
    content = env_example.read_text(encoding="utf-8")
    assert "DB_POOL_MAX" in content, ".env.example should contain DB_POOL_MAX"
    assert "REDIS_URL" in content, ".env.example should contain REDIS_URL"
    # REDIS_URL should be commented (optional)
    assert re.search(r"#\s*REDIS_URL", content), "REDIS_URL should be commented in .env.example"


def test_readme_documents_pool_redis():
    """README.md mentions connection pool and optional Redis."""
    readme = Path(__file__).parent.parent / "README.md"
    content = readme.read_text(encoding="utf-8")
    assert "psycopg_pool" in content or "connection pool" in content.lower(), "README should mention connection pooling"
    assert "DB_POOL_MAX" in content, "README should document DB_POOL_MAX"
    assert "Redis" in content, "README should mention Redis"
    assert "REDIS_URL" in content, "README should document REDIS_URL"


if __name__ == "__main__":
    # Run tests directly
    test_utcnow_replaced()
    print("✓ test_utcnow_replaced passed")
    test_dockerfile_no_volume_data()
    print("✓ test_dockerfile_no_volume_data passed")
    test_env_example_has_pool_redis()
    print("✓ test_env_example_has_pool_redis passed")
    test_readme_documents_pool_redis()
    print("✓ test_readme_documents_pool_redis passed")
    print("\nAll tests passed!")