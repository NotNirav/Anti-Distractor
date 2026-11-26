"""Unit tests for SettingsStore, PresetsStore, and safe .env GEMINI_API_KEY helpers."""

from __future__ import annotations

from pathlib import Path

from app.models import GoalProfile
from app.settings import (
    AppSettings,
    FocusPreset,
    PresetsStore,
    SettingsStore,
    clear_api_key_in_dotenv,
    is_placeholder_or_empty_api_key,
    mask_api_key,
    read_masked_api_key,
    write_api_key_to_dotenv,
)


def test_settings_store_roundtrip_and_sanitization(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path / "settings.json")
    assert store.exists() is False
    defaults = store.load()
    assert defaults == AppSettings(
        break_minutes=5,
        mute_sounds=False,
        timing_preset="dev",
        hotkeys_enabled=True,
        pomodoro_enabled=False,
        pomodoro_cycles=4,
    )

    saved = store.save(
        AppSettings(
            break_minutes=15,
            mute_sounds=True,
            timing_preset="demo",
            hotkeys_enabled=False,
            pomodoro_enabled=True,
            pomodoro_cycles=99,  # Clamped to 12
        )
    )
    assert saved.pomodoro_cycles == 12
    assert store.exists() is True

    reloaded = store.load()
    assert reloaded.break_minutes == 15
    assert reloaded.mute_sounds is True
    assert reloaded.timing_preset == "demo"
    assert reloaded.hotkeys_enabled is False
    assert reloaded.pomodoro_enabled is True
    assert reloaded.pomodoro_cycles == 12

    # Corrupt file falls back cleanly to defaults
    (tmp_path / "settings.json").write_text("{broken json!", encoding="utf-8")
    assert store.load() == AppSettings()


def test_dotenv_api_key_update_preserves_comments_and_masks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    env_file = tmp_path / ".env"

    configured, status = read_masked_api_key(env_file)
    assert configured is False
    assert status == "Not configured"

    env_file.write_text(
        "# Anti-Distractor configuration\n"
        "ANTI_DISTRACTOR_ENV=dev\n"
        "GEMINI_API_KEY=old_dummy_value_1111\n"
        "CUSTOM_FLAG=keep_me\n",
        encoding="utf-8",
    )

    configured, status = read_masked_api_key(env_file)
    assert configured is True
    assert "1111" in status
    assert "old_dummy_value" not in status

    write_api_key_to_dotenv(env_file, "dummy_new_key_9876\nINJECTED=bad", update_environ=False)
    content = env_file.read_text(encoding="utf-8")
    assert "# Anti-Distractor configuration" in content
    assert "ANTI_DISTRACTOR_ENV=dev" in content
    assert "CUSTOM_FLAG=keep_me" in content
    assert "GEMINI_API_KEY=dummy_new_key_9876INJECTED=bad" in content
    assert content.count("GEMINI_API_KEY=") == 1

    configured2, status2 = read_masked_api_key(env_file)
    assert configured2 is True
    assert mask_api_key("abcdefgh1234") == "••••••••1234"
    assert mask_api_key("short") == "••••••"


def test_presets_store_starter_seeding_and_crud(tmp_path: Path) -> None:
    store = PresetsStore(tmp_path / "presets.json")
    presets = store.list_presets()
    assert len(presets) >= 3
    names = [p.name for p in presets]
    assert "DSA Prep" in names
    assert "Deep Coding" in names
    assert "Writing & Docs" in names
    assert (tmp_path / "presets.json").is_file()

    custom = FocusPreset(
        name="ML Paper Reading",
        goal="Read Transformer Architecture Papers",
        duration_minutes=50,
        mode="normal",
        profile=GoalProfile(
            goal="Read Transformer Architecture Papers",
            on_task_topics=["attention", "transformers"],
            allowed_domains=["https://www.arxiv.org/", "openreview.net"],
            borderline_domains=["youtube.com"],
            blocked_domains=["instagram.com"],
            allowed_apps=["acrobat.exe"],
            blocked_apps=["steam.exe"],
        ),
    )
    after_add = store.upsert_preset(custom)
    assert any(p.name == "ML Paper Reading" for p in after_add)

    fetched = store.get_preset("ml paper reading")
    assert fetched is not None
    assert fetched.duration_minutes == 50
    assert "arxiv.org" in fetched.profile.allowed_domains

    # Update existing preset in place
    custom_updated = FocusPreset(
        name="ML Paper Reading",
        goal="Read Diffusion Papers",
        duration_minutes=60,
        mode="strict",
        profile=custom.profile,
    )
    after_upd = store.upsert_preset(custom_updated)
    assert len(after_upd) == len(after_add)
    assert store.get_preset("ML Paper Reading").duration_minutes == 60  # type: ignore[union-attr]

    # Delete preset
    after_del = store.delete_preset("ML Paper Reading")
    assert store.get_preset("ML Paper Reading") is None
    assert len(after_del) == len(after_add) - 1


def test_placeholder_detection_clear_dotenv_and_onboarding_flag(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert is_placeholder_or_empty_api_key("") is True
    assert is_placeholder_or_empty_api_key("   ") is True
    assert is_placeholder_or_empty_api_key("your_gemini_api_key_here") is True
    assert is_placeholder_or_empty_api_key("YOUR_API_KEY_HERE") is True
    assert is_placeholder_or_empty_api_key("AIzaSyDummyValidKey12345") is False

    env_file = tmp_path / ".env"
    env_file.write_text(
        "# Header comment\n"
        "ANTI_DISTRACTOR_ENV=dev\n"
        "GEMINI_API_KEY=your_gemini_api_key_here\n",
        encoding="utf-8",
    )
    configured, status = read_masked_api_key(env_file)
    assert configured is False
    assert status == "Not configured"

    write_api_key_to_dotenv(env_file, "AIzaSyDummyValidKey6789", update_environ=True)
    configured2, status2 = read_masked_api_key(env_file)
    assert configured2 is True
    assert "6789" in status2

    clear_api_key_in_dotenv(env_file, update_environ=True)
    content = env_file.read_text(encoding="utf-8")
    assert "# Header comment" in content
    assert "ANTI_DISTRACTOR_ENV=dev" in content
    assert "GEMINI_API_KEY=" not in content
    configured3, status3 = read_masked_api_key(env_file)
    assert configured3 is False
    assert status3 == "Not configured"

    settings_store = SettingsStore(tmp_path / "settings.json")
    saved = settings_store.save(AppSettings(break_minutes=10, onboarding_skipped=True))
    assert saved.onboarding_skipped is True
    reloaded = settings_store.load()
    assert reloaded.onboarding_skipped is True
    assert reloaded.break_minutes == 10
