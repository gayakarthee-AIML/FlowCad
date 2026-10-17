"""In-memory revision store, one history per session.

Hackathon scope: state is lost on restart and is not shared between
backend replicas. Generated files stay on disk in the output directory.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import datetime, timezone

from app.schemas.api import HistoryEntry, Revision

MAX_SESSIONS = 500
MAX_HISTORY = 50


class Session:
    def __init__(self) -> None:
        self.revisions: list[Revision] = []
        self.history: list[HistoryEntry] = []

    @property
    def current(self) -> Revision | None:
        return self.revisions[-1] if self.revisions else None


class RevisionStore:
    def __init__(self) -> None:
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.RLock()

    def session(self, session_id: str) -> Session:
        with self._lock:
            s = self._sessions.get(session_id)
            if s is None:
                s = self._sessions[session_id] = Session()
                while len(self._sessions) > MAX_SESSIONS:
                    self._sessions.popitem(last=False)
            self._sessions.move_to_end(session_id)
            return s

    def next_number(self, session_id: str) -> int:
        with self._lock:
            cur = self.session(session_id).current
            return (cur.number + 1) if cur else 1

    def commit(self, session_id: str, revision: Revision, summary: str) -> None:
        with self._lock:
            s = self.session(session_id)
            s.revisions.append(revision)
            s.history.append(HistoryEntry(revision=revision.number, status="committed",
                                          params=revision.params.model_dump(), summary=summary,
                                          created_at=revision.created_at))
            del s.revisions[:-MAX_HISTORY]
            del s.history[:-MAX_HISTORY]

    def reject(self, session_id: str, params: dict | None, summary: str) -> None:
        with self._lock:
            s = self.session(session_id)
            s.history.append(HistoryEntry(revision=None, status="rejected", params=params,
                                          summary=summary, created_at=datetime.now(timezone.utc)))
            del s.history[:-MAX_HISTORY]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()


store = RevisionStore()
