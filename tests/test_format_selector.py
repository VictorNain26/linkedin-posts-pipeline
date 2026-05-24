"""Tests du décideur de format (carousel/text/poll)."""

import sqlite3


def _seed_recent(conn, formats: list[str]) -> None:
    """Insère des posts factices avec les formats donnés (du plus récent au + ancien)."""
    for i, fmt in enumerate(formats):
        conn.execute(
            "INSERT INTO posts (published_at, topic, slug, format, keywords, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                f"2026-05-{20 - i:02d}T10:00:00",
                f"t{i}",
                f"s{i}",
                fmt,
                "[]",
                "published",
            ),
        )


class TestSelectFormat:
    def test_default_carousel_when_empty(self, tmp_data_dir):
        from format_selector import select_format

        fmt, reason = select_format()
        assert fmt == "carousel"
        assert "défaut" in reason.lower() or "carrousel" in reason.lower()

    def test_carousel_when_streak_below_limit(self, tmp_data_dir):
        import history
        from format_selector import select_format

        history.init_db()
        with sqlite3.connect(history.DB_PATH) as conn:
            _seed_recent(conn, ["carousel", "carousel"])

        fmt, _ = select_format()
        assert fmt == "carousel"

    def test_switches_after_3_carousels(self, tmp_data_dir):
        import history
        from format_selector import select_format

        history.init_db()
        with sqlite3.connect(history.DB_PATH) as conn:
            _seed_recent(conn, ["carousel", "carousel", "carousel"])

        fmt, reason = select_format()
        assert fmt != "carousel"
        assert fmt in ("text", "poll")
        assert "switch" in reason.lower() or "carrousels d" in reason.lower()

    def test_switches_to_text_after_streak(self, tmp_data_dir):
        """Polls retirés en mai 2026 (reach trap). Le switch carousel → text-only.
        Voir format_selector.py + config.py FORMAT_POLL deprecated."""
        import history
        from format_selector import select_format

        history.init_db()
        with sqlite3.connect(history.DB_PATH) as conn:
            _seed_recent(conn, ["carousel", "carousel", "carousel", "text"])

        fmt, _ = select_format()
        assert fmt == "text"
