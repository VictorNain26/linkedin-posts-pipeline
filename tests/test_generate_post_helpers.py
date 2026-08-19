"""Tests des helpers purs de generate_post (pas d'appel API)."""

import pytest

from generate_post import (
    _assemble_post_text,
    _detect_violations,
    _news_to_topic,
    _normalize_news_input,
    _normalize_punctuation,
    _slides_text,
    _strip_markdown,
    ensure_cta,
    extract_keywords,
    flatten_slides_to_strings,
    slugify,
)

CTA = "Vous voulez en discuter pour votre entreprise ? Mon DM est ouvert."


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
        out = ensure_cta(slides, CTA)
        combined = (out[-1].get("main", "") + " " + out[-1].get("sub", "")).lower()
        assert "dm" in combined or "discuter" in combined

    def test_idempotent_when_cta_present(self):
        slides = [{"main": "Mon DM est ouvert pour en discuter"}]
        out1 = ensure_cta(slides, CTA)
        out2 = ensure_cta(out1, CTA)
        assert out1[-1] == out2[-1]

    def test_empty_slides(self):
        assert ensure_cta([], CTA) == []


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

    def test_list_items_rendered(self):
        slides = [{"kind": "list", "main": "Vérifie :", "items": ["a", "b"]}]
        assert flatten_slides_to_strings(slides) == ["Vérifie :\n- a\n- b"]


class TestDetectViolations:
    def test_no_violation(self):
        text = _slides_text([{"main": "clean text"}, {"main": "no AI patterns"}])
        assert _detect_violations(text) == []

    def test_em_dash(self):
        text = _slides_text([{"main": "Hello — world"}])
        assert "—" in _detect_violations(text)

    def test_concretement(self):
        text = _slides_text([{"main": "Concrètement, voici"}])
        assert "Concrètement," in _detect_violations(text)

    def test_items_scanned(self):
        text = _slides_text([{"kind": "list", "main": "ok", "items": ["truc scalable"]}])
        assert "scalable" in _detect_violations(text)


class TestStripMarkdown:
    def test_removes_bold(self):
        # LinkedIn ne rend pas le markdown : "**Question 1**" s'afficherait littéralement
        assert _strip_markdown("**Question 1 :** le process marche ?") == "Question 1 : le process marche ?"

    def test_plain_text_unchanged(self):
        assert _strip_markdown("Pas de markdown ici.") == "Pas de markdown ici."

    def test_body_text_stripped_in_assembly(self):
        out = _assemble_post_text("Hook.", ["Corps avec **gras** dedans."], rotation_index=0)
        assert "**" not in out


class TestNormalizePunctuation:
    def test_em_dash_becomes_colon(self):
        assert _normalize_punctuation("forfaits par siège — accès via crédits") == (
            "forfaits par siège : accès via crédits"
        )

    def test_plain_text_unchanged(self):
        assert _normalize_punctuation("Pas de tiret ici.") == "Pas de tiret ici."

    def test_assembly_has_no_em_dash(self):
        out = _assemble_post_text("Hook — choc.", ["Corps — suite."], rotation_index=0)
        assert "—" not in out


class TestAssemblePostText:
    def test_carousel_body(self):
        out = _assemble_post_text("Le hook.", ["Ligne 1.", "Ligne 2."], rotation_index=0)
        parts = out.split("\n\n")
        assert parts[0] == "Le hook."
        assert parts[1] == "Ligne 1."
        assert parts[-1].startswith("#")  # hashtags en dernier

    def test_empty_suffix_skipped(self):
        # rotation_index=1 → CTA_POST_SUFFIXES[1] == "" → pas de bloc vide
        out = _assemble_post_text("Hook.", ["Corps."], rotation_index=1)
        assert "\n\n\n" not in out
        assert all(p.strip() for p in out.split("\n\n"))

    def test_hashtags_rotate(self):
        h0 = _assemble_post_text("H.", [], 0).split("\n\n")[-1]
        h1 = _assemble_post_text("H.", [], 1).split("\n\n")[-1]
        assert h0 != h1
        assert h0.startswith("#IntégrationIA #IA")
        assert h1.startswith("#IntégrationIA #IA")


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
