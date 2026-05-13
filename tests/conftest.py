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

    yield data_dir
