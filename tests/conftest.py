from __future__ import annotations

import pytest

from app.config import Settings
from app.data.repository import Repository


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def repo(settings: Settings) -> Repository:
    return Repository(settings.data_dir)
