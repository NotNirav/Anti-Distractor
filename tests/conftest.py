"""Pytest configuration and global fixtures for Anti-Distractor test suite."""

from unittest.mock import MagicMock
import google.genai as genai
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


@pytest.fixture(autouse=True)
def prevent_real_genai_calls(monkeypatch):
    """Prevent tests with dummy keys from hitting the real Google GenAI API."""
    orig_client = genai.Client

    def _safe_client(*args, **kwargs):
        key = kwargs.get("api_key", "")
        if key in ("test_api_key", "test_key", "fake-key", "mock_key", "mock"):
            mock = MagicMock()

            def _generate_content(**gen_kwargs):
                contents_str = str(gen_kwargs.get("contents", "")).lower()
                if "review" in contents_str or "summary" in contents_str:
                    return MagicMock(text="## Focus Session Review\n\nSession summary: Great focus.")
                return MagicMock(
                    text='{"activity": "Working", "on_task": true, "confidence": 0.9, "reason": "Test ok", "tabs_to_close": []}'
                )

            mock.models.generate_content.side_effect = _generate_content
            return mock
        return orig_client(*args, **kwargs)

    monkeypatch.setattr(genai, "Client", _safe_client)
