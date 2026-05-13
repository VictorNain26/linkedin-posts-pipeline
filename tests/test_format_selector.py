"""Tests du décideur de format (carousel/text/poll)."""

import sqlite3

import pytest

from config import MODE_EVERGREEN, MODE_VEILLE


def _seed_recent(conn, mode: str, formats: list[str]) -> None:
    """Insère des posts factices avec les formats donnés (du plus récent au + ancien)."""
    for i, fmt in enumerate(formats):
        conn.execute(
            "INSERT INTO posts (published_at, topic, slug, mode, format, keywords, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"2026-05-{20 - i:02d}T10:00:00",
                f"t{i}",
                f"s{i}",
                mode,
                fmt,
                "[]",
                "published",
            ),
        )


class TestSelectFormat:
    def test_evergreen_always_carousel(self, tmp_data_dir):
        from format_selector import select_format

        fmt, reason = select_format(MODE_EVERGREEN)
        assert fmt == "carousel"
        assert "always" in reason.lower() or "evergreen" in reason.lower()

    def test_veille_default_carousel(self, tmp_data_dir):
        from format_selector import select_format

        fmt, _ = select_format(MODE_VEILLE)
        assert fmt == "carousel"

    def test_veille_switches_after_3_carousels(self, tmp_data_dir):
        import history
        from format_selector import select_format

        history.init_db()
        with sqlite3.connect(history.DB_PATH) as conn:
            _seed_recent(conn, MODE_VEILLE, ["carousel", "carousel", "carousel"])

        fmt, reason = select_format(MODE_VEILLE)
        assert fmt != "carousel"
        assert fmt in ("text", "poll")
        assert "switching" in reason.lower()

    def test_veille_alternates_text_poll(self, tmp_data_dir):
        import history
        from format_selector import select_format

        history.init_db()
        # Streak avec last non-carousel = text → next switch doit être poll
        with sqlite3.connect(history.DB_PATH) as conn:
            _seed_recent(conn, MODE_VEILLE, ["carousel", "carousel", "carousel", "text"])

        fmt, _ = select_format(MODE_VEILLE)
        assert fmt == "poll"

    def test_unknown_mode_raises(self, tmp_data_dir):
        from format_selector import select_format

        with pytest.raises(ValueError):
            select_format("invalid-mode")
