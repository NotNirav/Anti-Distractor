"""Pytest configuration and global fixtures for Anti-Distractor test suite."""

import pytest
from app.judge import get_model_manager, reset_vision_cooldown


@pytest.fixture(autouse=True)
def reset_global_model_manager_between_tests():
    """Ensure global model manager state does not leak between unit tests."""
    get_model_manager().reset_preferences()
    reset_vision_cooldown()
    yield
    get_model_manager().reset_preferences()
    reset_vision_cooldown()
