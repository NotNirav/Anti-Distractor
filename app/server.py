"""FastAPI localhost bridge between the Chrome/Edge extension and the Python app."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Any, Callable, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.models import TabInfo, TabSnapshot, TriggerType
from app.profile import normalize_domain

logger = logging.getLogger(__name__)


class TabSnapshotStore:
    """Thread-safe holder for the latest browser tab snapshot and extension liveness."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: TabSnapshot | None = None
        self._last_seen_at: float | None = None

    def update(self, snapshot: TabSnapshot) -> None:
        """Replace the current tab snapshot and mark the extension as recently seen."""
        now = time.monotonic()
        with self._lock:
            self._latest = snapshot
            self._last_seen_at = now

    def mark_extension_seen(self) -> None:
        """Record that the browser extension contacted the local bridge."""
        now = time.monotonic()
        with self._lock:
            self._last_seen_at = now

    def is_extension_connected(self, stale_seconds: float = 8.0) -> bool:
        """Return True if the browser extension sent a snapshot or poll within stale_seconds."""
        now = time.monotonic()
        with self._lock:
            if self._last_seen_at is None:
                return False
            return (now - self._last_seen_at) <= stale_seconds

    def get_latest(self) -> TabSnapshot | None:
        """Return the latest TabSnapshot or None if no extension data has arrived yet."""
        with self._lock:
            return self._latest


class CommandQueue:
    """Thread-safe queue of pending extension commands (e.g., close_tabs) with ack tracking."""

    def __init__(self, ttl_seconds: float = 15.0) -> None:
        self._lock = threading.Lock()
        self.ttl_seconds = ttl_seconds
        self._pending: dict[str, dict[str, Any]] = {}

    def enqueue_close_tabs(self, tab_ids: list[int]) -> str:
        """Queue a close_tabs command and return its command_id."""
        with self._lock:
            cmd_id = f"cmd-{uuid.uuid4().hex[:8]}"
            unique_ids = list(dict.fromkeys(int(t) for t in tab_ids))
            self._pending[cmd_id] = {
                "command_id": cmd_id,
                "action": "close_tabs",
                "tab_ids": unique_ids,
                "created_at": time.time(),
                "in_flight_since": None,
            }
            return cmd_id

    def poll_commands(self) -> list[dict[str, Any]]:
        """Return pending commands for the extension and mark them in-flight."""
        now = time.time()
        with self._lock:
            # Expire commands in-flight beyond TTL to prevent stuck states
            expired = [
                cid
                for cid, c in self._pending.items()
                if c["in_flight_since"] is not None and (now - c["in_flight_since"] > self.ttl_seconds)
            ]
            for cid in expired:
                del self._pending[cid]

            result: list[dict[str, Any]] = []
            for c in self._pending.values():
                if c["in_flight_since"] is None:
                    c["in_flight_since"] = now
                    result.append(
                        {
                            "command_id": c["command_id"],
                            "action": c["action"],
                            "tab_ids": c["tab_ids"],
                        }
                    )
            return result

    def ack_command(self, command_id: str) -> None:
        """Remove an acknowledged command from the queue."""
        with self._lock:
            self._pending.pop(command_id, None)


class TabInfoModel(BaseModel):
    tab_id: int
    window_id: int
    title: str = ""
    url: str = ""
    domain: str = ""
    active: bool = False
    audible: bool = False


class TabEventPayload(BaseModel):
    event_type: str = "tab_activated"
    active_tab_id: int | None = None
    window_focused: bool = True
    timestamp: float = Field(default_factory=time.time)
    tabs: list[TabInfoModel] = Field(default_factory=list)


class CommandAckPayload(BaseModel):
    command_id: str
    closed_tab_ids: list[int] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class QuickRulePayload(BaseModel):
    action: Literal["allow", "block"]
    domain: str


def create_app(
    tab_store: TabSnapshotStore,
    command_queue: CommandQueue,
    on_tab_event: Callable[[TriggerType], None] | None = None,
    session_status_provider: Callable[[], tuple[bool, str | None]] | None = None,
    telemetry_provider: Callable[[str | None], dict[str, Any]] | None = None,
    on_quick_rule: Callable[[Literal["allow", "block"], str], dict[str, Any] | None] | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application with /health, /status, /tabs, /commands, /rules/quick."""
    app = FastAPI(title="Anti-Distractor Bridge")

    # Enable CORS for browser extensions and local origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _build_telemetry(domain: str | None = None) -> dict[str, Any]:
        session_active = False
        goal: str | None = None
        if session_status_provider is not None:
            try:
                session_active, goal = session_status_provider()
            except Exception:
                logger.exception("Error in session_status_provider callback")

        norm_dom = normalize_domain(domain or "")
        base: dict[str, Any] = {
            "status": "ok",
            "session_active": bool(session_active),
            "goal": goal,
            "mode": "strict",
            "state": "ON_TASK" if session_active else "IDLE",
            "paused": False,
            "remaining_seconds": 0.0,
            "pause_remaining_seconds": 0.0,
            "domain": norm_dom,
            "domain_rule": "neutral",
        }
        if telemetry_provider is not None:
            try:
                extra = telemetry_provider(norm_dom or None)
                if isinstance(extra, dict):
                    base.update(extra)
                    base["status"] = "ok"
            except Exception:
                logger.exception("Error in telemetry_provider callback")
        return base

    @app.get("/health")
    def health() -> dict[str, Any]:
        return _build_telemetry(None)

    @app.get("/status")
    def get_status(domain: str = Query(default="")) -> dict[str, Any]:
        tab_store.mark_extension_seen()
        return _build_telemetry(domain)

    @app.post("/rules/quick")
    def post_quick_rule(payload: QuickRulePayload) -> dict[str, Any]:
        tab_store.mark_extension_seen()
        norm_dom = normalize_domain(payload.domain)
        if not norm_dom:
            raise HTTPException(status_code=400, detail="Invalid or non-web domain.")
        if on_quick_rule is None:
            raise HTTPException(status_code=409, detail="Quick rules unavailable (no active session).")
        try:
            res = on_quick_rule(payload.action, norm_dom)
        except Exception as exc:
            logger.exception("Error applying quick rule for %s", norm_dom)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

        if isinstance(res, dict) and res.get("ok") is False:
            raise HTTPException(status_code=409, detail=str(res.get("error") or "No active session."))

        out: dict[str, Any] = {
            "ok": True,
            "action": payload.action,
            "domain": norm_dom,
            "domain_rule": "allowed" if payload.action == "allow" else "blocked",
        }
        if isinstance(res, dict):
            out.update(res)
        return out

    @app.post("/tabs")
    def post_tabs(payload: TabEventPayload) -> dict[str, Any]:
        tab_list: list[TabInfo] = []
        for t in payload.tabs:
            dom = t.domain or normalize_domain(t.url)
            tab_list.append(
                TabInfo(
                    tab_id=t.tab_id,
                    window_id=t.window_id,
                    title=t.title,
                    url=t.url,
                    domain=dom,
                    active=t.active,
                    audible=t.audible,
                )
            )

        snapshot = TabSnapshot(
            tabs=tab_list,
            active_tab_id=payload.active_tab_id,
            window_focused=payload.window_focused,
            updated_at=payload.timestamp or time.time(),
        )
        tab_store.update(snapshot)

        if on_tab_event is not None:
            valid_triggers: set[str] = {
                "baseline",
                "tab_activated",
                "tab_updated",
                "tab_created",
                "tab_removed",
                "window_changed",
                "heartbeat",
                "dwell",
                "manual_recheck",
            }
            trigger: TriggerType = (
                payload.event_type if payload.event_type in valid_triggers else "tab_updated"  # type: ignore[assignment]
            )
            try:
                on_tab_event(trigger)
            except Exception:
                logger.exception("Error in on_tab_event callback for trigger %s", trigger)

        return {"ok": True, "received_tabs": len(tab_list)}

    @app.get("/commands")
    def get_commands() -> dict[str, Any]:
        tab_store.mark_extension_seen()
        telemetry = _build_telemetry(None)
        return {
            "commands": command_queue.poll_commands(),
            "session": {
                "session_active": telemetry.get("session_active", False),
                "state": telemetry.get("state", "IDLE"),
                "paused": telemetry.get("paused", False),
            },
        }

    @app.post("/commands/ack")
    def post_commands_ack(payload: CommandAckPayload) -> dict[str, Any]:
        tab_store.mark_extension_seen()
        command_queue.ack_command(payload.command_id)
        return {"ok": True}

    return app


def start_server_thread(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> Any:
    """Start uvicorn in a background daemon thread and return the server handle."""
    config = uvicorn.Config(app=app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait up to 5.0 seconds for server.started
    start_time = time.time()
    while not getattr(server, "started", False):
        if not thread.is_alive():
            raise RuntimeError(
                f"Server thread terminated unexpectedly. Port {port} is likely already in use."
            )
        if time.time() - start_time > 5.0:
            server.should_exit = True
            raise RuntimeError(
                f"Server failed to start on http://{host}:{port} within 5.0 seconds. Port may be in use."
            )
        time.sleep(0.05)

    return server


if __name__ == "__main__":
    print("Starting Anti-Distractor Local Bridge on http://127.0.0.1:8765 ...")
    demo_store = TabSnapshotStore()
    demo_queue = CommandQueue()

    def _on_event(evt: TriggerType) -> None:
        snap = demo_store.get_latest()
        active_title = "(None)"
        if snap and snap.tabs:
            active = next((t for t in snap.tabs if t.active), snap.tabs[0])
            active_title = f"{active.title} ({active.domain})"
        print(f"[Browser Event: {evt}] Active: {active_title}")

    demo_app = create_app(
        demo_store,
        demo_queue,
        on_tab_event=_on_event,
        session_status_provider=lambda: (True, "Demo Focus Goal"),
    )
    uvicorn.run(demo_app, host="127.0.0.1", port=8765)
