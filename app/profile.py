"""Goal profile generation via Gemini and domain/title normalization helpers."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from google import genai
from google.genai import types
from pydantic import BaseModel

from app.config import GEMINI_VISION_MODEL
from app.models import GoalProfile


class _GoalProfileResponse(BaseModel):
    goal: str
    on_task_topics: list[str] = []
    allowed_domains: list[str] = []
    borderline_domains: list[str] = []
    blocked_domains: list[str] = []
    allowed_apps: list[str] = []
    blocked_apps: list[str] = []


def normalize_domain(url_or_domain: str) -> str:
    """Extract lowercase root/host domain without scheme or leading www."""
    raw = (url_or_domain or "").strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "http://" + raw
    try:
        parsed = urlparse(raw)
        host = parsed.netloc or parsed.path
        # Remove port if present
        host = host.split(":")[0].strip()
        # Remove leading www.
        if host.startswith("www."):
            host = host[4:]
        return host.strip("/").strip()
    except Exception:
        return url_or_domain.strip().lower()


def normalize_title(title: str) -> str:
    """Normalize a tab or window title for consistent verdict cache lookups."""
    if not title:
        return ""
    cleaned = title.replace("\u00a0", " ").strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def generate_goal_profile(goal: str, api_key: str) -> GoalProfile:
    """Call Gemini once at session start to expand a short goal into a GoalProfile."""
    if not api_key:
        return fallback_goal_profile(goal)

    prompt = f"""You are an expert focus and productivity assistant.
The user wants to focus on this goal: "{goal}".
Expand this goal into a structured GoalProfile with the following categories:
- on_task_topics: 5 to 10 specific concepts, sub-topics, or search keywords directly relevant to this goal.
- allowed_domains: Root website domains that are definitely on-task for this goal (e.g. "leetcode.com", "github.com", "docs.python.org"). Do not include "www." or "https://".
- borderline_domains: General platforms where content could be either on-task or distracting depending on the specific video/thread (e.g. "youtube.com", "reddit.com", "chatgpt.com", "medium.com").
- blocked_domains: Popular distracting websites that are clearly off-task for this goal (e.g. "instagram.com", "tiktok.com", "x.com", "twitter.com", "facebook.com", "netflix.com", "twitch.tv").
- allowed_apps: Desktop executable names likely used for this task (e.g. "code.exe", "devenv.exe", "windowsterminal.exe", "pycharm64.exe").
- blocked_apps: Desktop executable names that represent games or non-work distractions (e.g. "steam.exe", "discord.exe", "spotify.exe").
"""
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_VISION_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=_GoalProfileResponse,
            ),
        )
        data = json.loads(response.text)
        return GoalProfile(
            goal=goal,
            on_task_topics=[t.strip() for t in data.get("on_task_topics", []) if t.strip()],
            allowed_domains=[normalize_domain(d) for d in data.get("allowed_domains", []) if d.strip()],
            borderline_domains=[normalize_domain(d) for d in data.get("borderline_domains", []) if d.strip()],
            blocked_domains=[normalize_domain(d) for d in data.get("blocked_domains", []) if d.strip()],
            allowed_apps=[a.strip().lower() for a in data.get("allowed_apps", []) if a.strip()],
            blocked_apps=[a.strip().lower() for a in data.get("blocked_apps", []) if a.strip()],
        )
    except Exception:
        return fallback_goal_profile(goal)


def fallback_goal_profile(goal: str) -> GoalProfile:
    """Return a safe default GoalProfile if the Gemini call fails or hits rate limits."""
    return GoalProfile(
        goal=goal,
        on_task_topics=[goal.strip()],
        allowed_domains=["google.com", "wikipedia.org", "stackoverflow.com", "github.com", "leetcode.com"],
        borderline_domains=["youtube.com", "reddit.com", "chatgpt.com", "claude.ai", "medium.com"],
        blocked_domains=["instagram.com", "tiktok.com", "twitter.com", "x.com", "facebook.com", "netflix.com", "twitch.tv"],
        allowed_apps=["code.exe", "devenv.exe", "pycharm64.exe", "windowsterminal.exe", "notepad.exe", "cmd.exe", "powershell.exe"],
        blocked_apps=["steam.exe", "epicgameslauncher.exe", "discord.exe", "spotify.exe"],
    )
