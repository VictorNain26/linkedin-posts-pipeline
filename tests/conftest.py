"""Configuration commune pytest — fixtures, env vars de test."""

import os
import sys
from pathlib import Path

# Permettre d'importer les modules du projet sans installer en package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Env vars de test (jamais propagées en prod)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-fake")
os.environ.setdefault("LI_ACCESS_TOKEN", "fake-token")
os.environ.setdefault("LI_PERSON_URN", "urn:li:person:fake")
os.environ.setdefault("LI_CLIENT_ID", "fake-client")
os.environ.setdefault("LI_CLIENT_SECRET", "fake-secret")


import pytest


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Isole LINKEDIN_DATA_DIR + DB_PATH vers un dir temp par test.

    Important : reload config + history pour que DB_PATH soit ré-évalué avec le
    nouveau LINKEDIN_DATA_DIR (sinon module-level state pollué entre tests).
    """
    data_dir = tmp_path / "linkedin-data"
    data_dir.mkdir()
    monkeypatch.setenv("LINKEDIN_DATA_DIR", str(data_dir))

    import importlib

    import config
    import format_selector
    import history

    importlib.reload(config)
    importlib.reload(history)
    importlib.reload(format_selector)

    # Reload aussi les modules qui ont importé LEARNINGS_PATH au module-level
    # (sinon ils gardent la référence à l'ancienne valeur avant monkeypatch)
    try:
        import agents.system
        importlib.reload(agents.system)
    except ImportError:
        pass
    try:
        import import_analytics_csv
        importlib.reload(import_analytics_csv)
    except ImportError:
        pass

    # Init explicite de la DB pour les tests qui font des INSERT sans passer par un helper
    history.init_db()

    yield data_dir
