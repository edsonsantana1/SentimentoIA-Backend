import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

# Isola a execucao de testes em um database dedicado.
os.environ.setdefault("ENV", "development")
os.environ.setdefault("DATABASE_NAME", "sentimento_db_pytest")
os.environ.setdefault("ENABLE_DEV_CLEAR_DATA", "true")

from app.main import app
from app.database import MongoDB


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as test_client:
        yield test_client

    if MongoDB.client is not None:
        topology = getattr(MongoDB.client, "_topology", None)
        if topology is not None and not getattr(topology, "_closed", False):
            MongoDB.client.drop_database(os.environ["DATABASE_NAME"])
