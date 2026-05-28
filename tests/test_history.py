"""Tests des opérations SQLite history."""


class TestRecordPost:
    def test_basic_insert(self, tmp_data_dir):
        import history

        post_id = history.record_post(
            topic="Test topic",
            slug="test-topic",
            format="carousel",
            keywords=["ia", "pme"],
            linkedin_post_id="urn:li:ugcPost:123",
        )
        assert post_id > 0

    def test_posted_today_false_when_empty(self, tmp_data_dir):
        import history

        assert history.posted_today() is False

    def test_posted_today_true_after_record(self, tmp_data_dir):
        import history

        history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        assert history.posted_today() is True


class TestRecentPublishedTopics:
    def test_empty_when_no_history(self, tmp_data_dir):
        import history

        assert history.recent_published_topics() == []

    def test_returns_title_part_most_recent_first(self, tmp_data_dir):
        import history

        history.record_post(
            topic="Sujet A. Résumé long de l'article A.",
            slug="a",
            format="carousel",
            keywords=["ia"],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        history.record_post(
            topic="Sujet B. Résumé de B.",
            slug="b",
            format="carousel",
            keywords=["rag"],
            linkedin_post_id="urn:li:ugcPost:2",
        )
        topics = history.recent_published_topics()
        # Titre seul (avant le 1er point), plus récent en tête.
        assert topics[0] == "Sujet B"
        assert "Sujet A" in topics

    def test_excludes_non_published(self, tmp_data_dir):
        import history

        history.record_post(
            topic="Brouillon. test.",
            slug="d",
            format="carousel",
            keywords=[],
            status="test",
            linkedin_post_id="urn:li:ugcPost:3",
        )
        assert history.recent_published_topics() == []


class TestHookVariants:
    def test_record_and_count_winner(self, tmp_data_dir):
        import history

        post_id = history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        variants = [
            {"formula": "contrarian", "hook": "h1"},
            {"formula": "data", "hook": "h2"},
            {"formula": "prospect_question", "hook": "h3"},
        ]
        history.record_hook_variants(
            post_id=post_id,
            variants=variants,
            winner_formula="data",
            judge_reason="Stat hook stops scroll best",
        )

        stats = history.formula_win_rate()
        assert "data" in stats
        assert stats["data"]["picked"] == 1


class TestRecentWinningHooks:
    def test_empty_when_no_posts(self, tmp_data_dir):
        import history

        assert history.recent_winning_hooks() == []

    def test_returns_only_winners_recent_first(self, tmp_data_dir):
        import history

        for i in range(2):
            pid = history.record_post(
                topic=f"t{i}",
                slug=f"s{i}",
                format="carousel",
                keywords=[],
                linkedin_post_id=f"urn:li:ugcPost:{i}",
            )
            history.record_hook_variants(
                post_id=pid,
                variants=[
                    {"formula": "contrarian", "hook": f"loser-{i}"},
                    {"formula": "data", "hook": f"winner-{i}"},
                ],
                winner_formula="data",
                judge_reason="r",
            )
        hooks = history.recent_winning_hooks(limit=8)
        # Seuls les gagnants, ordre du plus récent au plus ancien
        assert hooks == ["winner-1", "winner-0"]

    def test_respects_limit(self, tmp_data_dir):
        import history

        for i in range(3):
            pid = history.record_post(
                topic=f"t{i}",
                slug=f"s{i}",
                format="carousel",
                keywords=[],
                linkedin_post_id=f"urn:li:ugcPost:{i}",
            )
            history.record_hook_variants(
                post_id=pid,
                variants=[{"formula": "data", "hook": f"winner-{i}"}],
                winner_formula="data",
                judge_reason="r",
            )
        assert len(history.recent_winning_hooks(limit=2)) == 2


class TestAnalytics:
    def test_upsert_and_retrieve(self, tmp_data_dir):
        import history

        post_id = history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        history.upsert_analytics(post_id, "IMPRESSION", 1234)
        history.upsert_analytics(post_id, "REACTION", 56)

        latest = history.latest_analytics(post_id)
        assert latest["IMPRESSION"] == 1234
        assert latest["REACTION"] == 56

    def test_posts_to_fetch_filters_published(self, tmp_data_dir):
        import history

        history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:1",
            status="published",
        )
        history.record_post(
            topic="draft",
            slug="d",
            format="carousel",
            keywords=[],
            linkedin_post_id="urn:li:ugcPost:2",
            status="draft",
        )
        posts = history.posts_to_fetch_analytics()
        assert len(posts) == 1
        assert posts[0][1] == "urn:li:ugcPost:1"


class TestFormatHistory:
    def test_recent_formats(self, tmp_data_dir):
        import history

        for i, fmt in enumerate(["carousel", "text", "poll"]):
            history.record_post(
                topic=f"t{i}",
                slug=f"s{i}",
                format=fmt,
                keywords=[],
                linkedin_post_id=f"urn:li:ugcPost:{i}",
            )
        recent = history.recent_formats(limit=5)
        # Plus récent en premier
        assert recent == ["poll", "text", "carousel"]
