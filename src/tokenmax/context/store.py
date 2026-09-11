from __future__ import annotations

from collections import defaultdict

from ..models import Message


class ConversationStore:
    """In-memory transcript store.

    The store keeps the *full* history; the assembled prompt is always a derived
    view. That separation is what makes pruning and compaction safe - nothing the
    user said is ever destroyed, it just stops being sent.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, list[Message]] = defaultdict(list)

    def append(self, session_id: str, message: Message) -> Message:
        self._sessions[session_id].append(message)
        return message

    def extend(self, session_id: str, messages: list[Message]) -> None:
        self._sessions[session_id].extend(messages)

    def history(self, session_id: str) -> list[Message]:
        return list(self._sessions[session_id])

    def replace(self, session_id: str, messages: list[Message]) -> None:
        self._sessions[session_id] = list(messages)

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def sessions(self) -> list[str]:
        return list(self._sessions)
