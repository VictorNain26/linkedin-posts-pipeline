"""Tests pour _safe_filename + _safe_upload_path (dashboard R2 — anti path traversal)."""

import importlib
import sys
from pathlib import Path

import pytest


def _import_safety_helpers():
    """Import isolé sans charger Streamlit (qui spam les warnings en bare mode).

    Helpers définis directement (mêmes que ceux de dashboard.py) — on teste la
    logique pure, pas l'intégration UI.
    """
    import re
    _SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

    def _safe_filename(raw: str, fallback: str = "upload") -> str:
        cleaned = _SAFE_FILENAME_RE.sub("_", raw).strip("._-")
        return cleaned[:100] or fallback

    def _safe_upload_path(upload_dir: Path, raw_filename: str) -> Path:
        upload_dir = upload_dir.resolve()
        upload_dir.mkdir(parents=True, exist_ok=True)
        safe_name = _safe_filename(raw_filename)
        candidate = (upload_dir / safe_name).resolve()
        candidate.relative_to(upload_dir)  # raise ValueError si évasion
        return candidate

    return _safe_filename, _safe_upload_path


class TestSafeFilename:
    def setup_method(self):
        self._safe_filename, _ = _import_safety_helpers()

    @pytest.mark.parametrize("raw,expected", [
        ("normal.xlsx", "normal.xlsx"),
        ("Contenu_2026-05-18_VictorLenain.xlsx", "Contenu_2026-05-18_VictorLenain.xlsx"),
        ("file with spaces.csv", "file_with_spaces.csv"),
        ("file-with-dashes.txt", "file-with-dashes.txt"),
        ("file.with.many.dots.xlsx", "file.with.many.dots.xlsx"),
    ])
    def test_legitimate_names_preserved(self, raw, expected):
        assert self._safe_filename(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("../../etc/passwd", "etc_passwd"),
        ("..", "upload"),
        ("../", "upload"),
        ("/etc/passwd", "etc_passwd"),
        ("..\\..\\Windows\\System32", "Windows_System32"),
    ])
    def test_path_traversal_neutralized(self, raw, expected):
        assert self._safe_filename(raw) == expected

    @pytest.mark.parametrize("raw,expected", [
        ("évil; rm -rf /.xlsx", "vil_rm_-rf_.xlsx"),
        ("$(curl evil.com)", "curl_evil.com"),
        ("file&name|pipe.txt", "file_name_pipe.txt"),
        ("file\nname.txt", "file_name.txt"),
    ])
    def test_shell_metacharacters_stripped(self, raw, expected):
        assert self._safe_filename(raw) == expected

    def test_empty_input_returns_fallback(self):
        assert self._safe_filename("") == "upload"
        assert self._safe_filename("   ") == "upload"
        assert self._safe_filename("___") == "upload"

    def test_custom_fallback(self):
        assert self._safe_filename("", fallback="default.xlsx") == "default.xlsx"

    def test_truncation_at_100_chars(self):
        long = "a" * 200 + ".xlsx"
        result = self._safe_filename(long)
        assert len(result) == 100


class TestSafeUploadPath:
    def setup_method(self):
        _, self._safe_upload_path = _import_safety_helpers()

    def test_legitimate_path(self, tmp_path):
        result = self._safe_upload_path(tmp_path, "good.xlsx")
        assert result == (tmp_path / "good.xlsx").resolve()

    def test_path_traversal_blocked(self, tmp_path):
        # Le sanitize convertit ../etc/passwd → etc_passwd → reste dans tmp_path
        result = self._safe_upload_path(tmp_path, "../../etc/passwd")
        assert result.parent == tmp_path.resolve()
        assert "passwd" in result.name
        assert ".." not in str(result)

    def test_creates_upload_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "doesnt_exist_yet"
        assert not new_dir.exists()
        self._safe_upload_path(new_dir, "test.xlsx")
        assert new_dir.exists()
