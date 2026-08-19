"""Tests des fixes P0 : matching analytics par activity_id/date, monotonie des
imports fenêtrés, fusion des doublons external, et rotation des registres."""


class TestUpsertAnalyticsMonotonic:
    def test_accepts_increasing_values(self, tmp_data_dir):
        import history

        pid = history.record_post(topic="t", slug="s", format="carousel", keywords=[])
        assert history.upsert_analytics(pid, "IMPRESSION", 100) is True
        assert history.upsert_analytics(pid, "IMPRESSION", 250) is True

    def test_rejects_windowed_lower_value(self, tmp_data_dir):
        """Un export fenêtré (7j) d'un vieux post remonte 3 impressions alors que le
        cumul réel est 1383 — la valeur inférieure ne doit pas écraser le cumul."""
        import history

        pid = history.record_post(topic="t", slug="s", format="carousel", keywords=[])
        assert history.upsert_analytics(pid, "IMPRESSION", 1383) is True
        assert history.upsert_analytics(pid, "IMPRESSION", 3) is False

    def test_monotonic_per_metric(self, tmp_data_dir):
        import history

        pid = history.record_post(topic="t", slug="s", format="carousel", keywords=[])
        history.upsert_analytics(pid, "IMPRESSION", 1000)
        # INTERACTION indépendante : 5 < 1000 ne doit pas être rejeté
        assert history.upsert_analytics(pid, "INTERACTION", 5) is True


class TestPurgeNonMonotonic:
    def test_removes_legacy_windowed_rows(self, tmp_data_dir):
        import datetime

        import history

        pid = history.record_post(topic="t", slug="s", format="carousel", keywords=[])
        # Insertion brute avec timestamps contrôlés (simule l'historique pollué)
        with history._conn() as conn:
            t0 = datetime.datetime(2026, 5, 25).isoformat()
            t1 = datetime.datetime(2026, 6, 4).isoformat()
            conn.execute(
                "INSERT INTO post_analytics (post_id, metric, count, fetched_at) VALUES (?, ?, ?, ?)",
                (pid, "IMPRESSION", 1383, t0),
            )
            conn.execute(
                "INSERT INTO post_analytics (post_id, metric, count, fetched_at) VALUES (?, ?, ?, ?)",
                (pid, "IMPRESSION", 3, t1),
            )
        removed = history.purge_non_monotonic_analytics()
        assert removed == 1
        with history._conn() as conn:
            rows = conn.execute("SELECT count FROM post_analytics WHERE post_id = ?", (pid,)).fetchall()
        assert [r[0] for r in rows] == [1383]


class TestMatchByDate:
    def test_xlsx_activity_id_matches_published_post_by_date(self, tmp_data_dir):
        """Le bug racine : l'API stocke urn:li:ugcPost:M, l'export XLSX expose
        urn:li:activity:N (IDs différents). Le match par date doit rattacher les
        métriques au post pipeline, puis mémoriser l'activity_id."""
        import datetime

        import history
        import import_analytics_csv as iac

        pid = history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:7470760384258445312",
            status="published",
        )
        today = datetime.date.today().isoformat()
        post = {
            "url": "https://www.linkedin.com/feed/update/urn:li:activity:9999888877776666555/",
            "activity_id": "9999888877776666555",
            "date": today,
            "impressions": 100,
            "interactions": 5,
        }
        matched = iac._match_or_create_post(post)
        assert matched == pid
        # L'activity_id est mémorisé → match exact aux imports suivants
        assert history.find_post_by_activity_id("9999888877776666555") == pid

    def test_unmatched_creates_external(self, tmp_data_dir):
        import import_analytics_csv as iac

        post = {
            "url": "https://www.linkedin.com/feed/update/urn:li:activity:1111/",
            "activity_id": "1111222233334444555",
            "date": "2026-03-17",
            "impressions": 7475,
            "interactions": 26,
        }
        pid = iac._match_or_create_post(post)
        import history

        with history._conn() as conn:
            row = conn.execute("SELECT status FROM posts WHERE id = ?", (pid,)).fetchone()
        assert row[0] == "external"


class TestHealExternalDuplicates:
    def test_merges_external_into_published_same_date(self, tmp_data_dir):
        import history
        import import_analytics_csv as iac

        pub = history.record_post(
            topic="vrai post",
            slug="vrai",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:111",
            status="published",
        )
        # Doublon external créé par le matching legacy, daté du même jour
        import datetime

        today = datetime.date.today().isoformat()
        with history._conn() as conn:
            cur = conn.execute(
                """INSERT INTO posts (published_at, topic, slug, format, keywords,
                       linkedin_post_id, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    today + "T00:00:00",
                    "[external] dup",
                    "external-222",
                    "unknown",
                    "[]",
                    "urn:li:activity:222",
                    "external",
                ),
            )
            ext = cur.lastrowid
        history.upsert_analytics(ext, "IMPRESSION", 500)

        result = iac.heal_external_duplicates()
        assert result["externals_merged"] == 1
        with history._conn() as conn:
            # Analytics réassignées au post publié, doublon supprimé
            n = conn.execute("SELECT COUNT(*) FROM post_analytics WHERE post_id = ?", (pub,)).fetchone()[0]
            gone = conn.execute("SELECT COUNT(*) FROM posts WHERE id = ?", (ext,)).fetchone()[0]
        assert n == 1
        assert gone == 0
        # activity_id récupéré depuis le doublon
        assert history.find_post_by_activity_id("222") == pub


class TestSelectRegistre:
    def test_first_post_is_pedagogie(self, tmp_data_dir):
        import format_selector

        registre, _ = format_selector.select_registre(stories_available=False)
        assert registre == "pedagogie"

    def test_preuve_skipped_without_stories(self, tmp_data_dir):
        import format_selector
        import history

        history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            status="published",
            registre="pedagogie",
        )
        registre, reason = format_selector.select_registre(stories_available=False)
        assert registre == "pain"
        assert "preuve sautée" in reason

    def test_lru_rotation_with_stories(self, tmp_data_dir):
        import format_selector
        import history

        for r in ("pedagogie", "pain"):
            history.record_post(
                topic="t",
                slug="s",
                format="carousel",
                keywords=[],
                status="published",
                registre=r,
            )
        registre, _ = format_selector.select_registre(stories_available=True)
        assert registre == "preuve"


class TestTargetFormulaRotation:
    def test_lru_formula(self, tmp_data_dir):
        import generate_post
        import history

        pid = history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            status="published",
        )
        history.record_hook_variants(
            post_id=pid,
            variants=[
                {"formula": "contrarian", "hook": "h1"},
                {"formula": "data", "hook": "h2"},
                {"formula": "prospect_question", "hook": "h3"},
            ],
            winner_formula="contrarian",
            judge_reason="test",
        )
        # contrarian vient de gagner → la cible doit être une autre formule
        assert generate_post._select_target_formula() in ("data", "prospect_question")
