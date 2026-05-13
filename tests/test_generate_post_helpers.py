"""Tests des helpers purs de generate_post (pas d'appel API)."""

import pytest

from generate_post import (
    _detect_violations,
    _news_to_topic,
    _normalize_news_input,
    ensure_cta,
    extract_keywords,
    flatten_slides_to_strings,
    slugify,
)


class TestSlugify:
    def test_simple_lower(self):
        assert slugify("Hello World") == "hello-world"

    def test_french_accents(self):
        assert slugify("Ça Marche Très Bien") == "ca-marche-tres-bien"

    def test_ligatures(self):
        assert slugify("Œuvre & Solution") == "oeuvre-solution"

    def test_empty_input(self):
        assert slugify("!!!") == ""

    def test_max_length(self):
        long = "x" * 200
        assert len(slugify(long)) <= 50

    def test_strip_dashes(self):
        assert slugify("--foo--") == "foo"


class TestEnsureCta:
    def test_adds_cta_when_missing(self):
        slides = [{"main": "hook"}, {"main": "last slide", "sub": ""}]
        out = ensure_cta(slides)
        combined = (out[-1].get("main", "") + " " + out[-1].get("sub", "")).lower()
        assert "dm" in combined or "discuter" in combined

    def test_idempotent_when_cta_present(self):
        slides = [{"main": "Mon DM est ouvert pour en discuter"}]
        out1 = ensure_cta(slides)
        out2 = ensure_cta(out1)
        assert out1[-1] == out2[-1]

    def test_empty_slides(self):
        assert ensure_cta([]) == []


class TestExtractKeywords:
    def test_basic(self):
        keys = extract_keywords("IA pour PME", [{"main": "intégration Claude", "sub": ""}])
        assert "claude" in keys
        assert "pme" not in keys  # 3 chars, sous le seuil

    def test_dedup(self):
        keys = extract_keywords("test test test", [{"main": "test test"}])
        assert keys.count("test") == 1

    def test_max_20(self):
        long_words = " ".join(f"word{i:03d}" for i in range(30))
        keys = extract_keywords(long_words, [])
        assert len(keys) <= 20


class TestFlattenSlides:
    def test_main_only(self):
        assert flatten_slides_to_strings([{"main": "foo"}]) == ["foo"]

    def test_main_plus_sub(self):
        assert flatten_slides_to_strings([{"main": "foo", "sub": "bar"}]) == ["foo\nbar"]

    def test_empty_sub_ignored(self):
        assert flatten_slides_to_strings([{"main": "foo", "sub": ""}]) == ["foo"]


class TestDetectViolations:
    def test_no_violation(self):
        slides = [{"main": "clean text"}, {"main": "no AI patterns"}]
        assert _detect_violations(slides) == []

    def test_em_dash(self):
        slides = [{"main": "Hello — world"}]
        assert "—" in _detect_violations(slides)

    def test_concretement(self):
        slides = [{"main": "Concrètement, voici"}]
        assert "Concrètement," in _detect_violations(slides)


class TestNormalizeNewsInput:
    def test_none(self):
        assert _normalize_news_input(None) == []

    def test_empty_list(self):
        assert _normalize_news_input([]) == []

    def test_single_dict(self):
        n = {"title": "T", "summary": "S"}
        assert _normalize_news_input(n) == [n]

    def test_list_of_dicts(self):
        n = [{"title": "T1"}, {"title": "T2"}]
        assert _normalize_news_input(n) == n

    def test_filter_non_dicts(self):
        assert _normalize_news_input([{"title": "ok"}, "string", 42]) == [{"title": "ok"}]

    def test_string_wrapped(self):
        assert _normalize_news_input("hello") == [{"title": "hello", "summary": "", "url": ""}]


class TestNewsToTopic:
    def test_full(self):
        assert _news_to_topic({"title": "Hello", "summary": "World"}) == "Hello. World"

    def test_title_only(self):
        assert _news_to_topic({"title": "Hello", "summary": ""}) == "Hello"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            _news_to_topic({"title": "", "summary": ""})
