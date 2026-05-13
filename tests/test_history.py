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


class TestKeywordOverlap:
    def test_zero_with_empty_history(self, tmp_data_dir):
        import history

        assert history.keyword_overlap_ratio(["ia", "claude"]) == 0.0

    def test_zero_with_empty_new(self, tmp_data_dir):
        import history

        history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=["ia"],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        assert history.keyword_overlap_ratio([]) == 0.0

    def test_partial_overlap(self, tmp_data_dir):
        import history

        history.record_post(
            topic="t",
            slug="s",
            format="carousel",
            keywords=["ia", "claude", "rag"],
            linkedin_post_id="urn:li:ugcPost:1",
        )
        # 1 commun sur 3 nouveaux = 0.333
        ratio = history.keyword_overlap_ratio(["ia", "nodejs", "docker"])
        assert 0.3 < ratio < 0.4


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
