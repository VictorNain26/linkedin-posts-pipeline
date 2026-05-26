"""Tests pour les nouvelles tables history : follower_growth + audience_snapshot.

Couvre les helpers : upsert_follower_growth, insert_audience_snapshot,
follower_growth_summary, latest_audience_snapshot.
"""


def test_follower_growth_upsert_inserts(tmp_data_dir):
    from history import follower_growth_summary, upsert_follower_growth

    upsert_follower_growth("2026-05-18", 3, total_followers=None)
    upsert_follower_growth("2026-05-19", 7, total_followers=1005)

    summary = follower_growth_summary(days=30)
    assert summary["days_covered"] == 2
    assert summary["total_new_followers"] == 10
    assert summary["last_known_total"] == 1005
    assert summary["daily_avg"] == 5.0


def test_follower_growth_upsert_updates(tmp_data_dir):
    """Idempotence : ré-insérer la même date update au lieu de dupliquer."""
    from history import follower_growth_summary, upsert_follower_growth

    upsert_follower_growth("2026-05-18", 3)
    upsert_follower_growth("2026-05-18", 5)  # update : remplace 3 par 5

    summary = follower_growth_summary(days=30)
    assert summary["days_covered"] == 1
    assert summary["total_new_followers"] == 5


def test_follower_growth_summary_empty(tmp_data_dir):
    from history import follower_growth_summary

    summary = follower_growth_summary(days=30)
    assert summary["days_covered"] == 0
    assert summary["total_new_followers"] == 0
    assert summary["last_known_total"] is None


def test_audience_snapshot_insert(tmp_data_dir):
    from history import insert_audience_snapshot, latest_audience_snapshot

    snapshot_at = "2026-05-24T12:00:00"
    rows = [
        ("Lieux", "Paris", 0.51),
        ("Lieux", "Lyon", 0.04),
        ("Intitulés de poste", "Développeur", 0.05),
    ]
    insert_audience_snapshot(snapshot_at, rows)

    result = latest_audience_snapshot()
    assert "Lieux" in result
    assert "Intitulés de poste" in result
    # Paris doit être en tête de Lieux (ordered DESC by percentage)
    assert result["Lieux"][0][0] == "Paris"
    assert result["Lieux"][0][1] == 0.51


def test_audience_snapshot_wipe_on_resnapshot(tmp_data_dir):
    """Insert deux fois sur la même date → la 2e wipe la 1re."""
    from history import insert_audience_snapshot, latest_audience_snapshot

    snapshot_at = "2026-05-24T12:00:00"
    insert_audience_snapshot(snapshot_at, [("Lieux", "Paris", 0.51)])
    insert_audience_snapshot(snapshot_at, [("Lieux", "Berlin", 0.40)])

    result = latest_audience_snapshot()
    assert result["Lieux"][0][0] == "Berlin"  # le 2e insert a remplacé
    assert len(result["Lieux"]) == 1


def test_latest_audience_snapshot_empty(tmp_data_dir):
    from history import latest_audience_snapshot

    assert latest_audience_snapshot() == {}
