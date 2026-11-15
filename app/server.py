"""FastAPI localhost bridge between the Chrome/Edge extension and the Python app."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import FastAPI

from app.models import TabSnapshot, TriggerType


class TabSnapshotStore:
    """Thread-safe holder for the latest browser tab snapshot."""

    def update(self, snapshot: TabSnapshot) -> None:
        """Replace the current tab snapshot."""
        # TODO(T2.1): Store latest TabSnapshot under a threading.Lock.
        raise NotImplementedError("TODO(T2.1): Implement TabSnapshotStore.update")

    def get_latest(self) -> TabSnapshot | None:
        """Return the latest TabSnapshot or None if no extension data has arrived yet."""
        raise NotImplementedError("TODO(T2.1): Implement TabSnapshotStore.get_latest")


class CommandQueue:
    """Thread-safe queue of pending extension commands (e.g., close_tabs) with ack tracking."""

    def enqueue_close_tabs(self, tab_ids: list[int]) -> str:
        """Queue a close_tabs command and return its command_id."""
        # TODO(T2.1): Generate command_id and store pending close_tabs command.
        raise NotImplementedError("TODO(T2.1): Implement CommandQueue.enqueue_close_tabs")

    def poll_commands(self) -> list[dict[str, Any]]:
        """Return pending commands for the extension and mark them in-flight."""
        raise NotImplementedError("TODO(T2.1): Implement CommandQueue.poll_commands")

    def ack_command(self, command_id: str) -> None:
        """Remove an acknowledged command from the queue."""
        raise NotImplementedError("TODO(T2.1): Implement CommandQueue.ack_command")


def create_app(
    tab_store: TabSnapshotStore,
    command_queue: CommandQueue,
    on_tab_event: Callable[[TriggerType], None] | None = None,
    session_status_provider: Callable[[], tuple[bool, str | None]] | None = None,
) -> FastAPI:
    """Build and configure the FastAPI application with /health, /tabs, /commands, /commands/ack."""
    # TODO(T2.1): Register routes and CORS middleware.
    raise NotImplementedError("TODO(T2.1): Implement create_app")


def start_server_thread(app: FastAPI, host: str = "127.0.0.1", port: int = 8765) -> Any:
    """Start uvicorn in a background daemon thread and return the server handle."""
    # TODO(T2.1): Launch uvicorn.Server in a daemon thread.
    raise NotImplementedError("TODO(T2.1): Implement start_server_thread")
