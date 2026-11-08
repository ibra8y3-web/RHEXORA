from pathlib import Path

from scripts.collect_build_failure import extract_referenced_files, safe_repo_path


def test_rejects_traversal_and_secret_files():
    assert safe_repo_path("../../etc/passwd") is None
    assert safe_repo_path(".env") is None
    assert safe_repo_path("secrets/token.txt") is None


def test_extracts_existing_repo_file_paths():
    log = "src/rhexora/cli.py:42: error: invalid option\n"
    found = extract_referenced_files(log)
    assert "src/rhexora/cli.py" in found
    assert 42 in found["src/rhexora/cli.py"]


def test_ignores_nonexistent_paths():
    assert extract_referenced_files("src/not-a-real-file.py:12: error") == {}
