from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .model import SearchQuery


@dataclass(frozen=True)
class RecordingResult:
    ok: bool
    previous_state: str
    current_state: str
    modal_status: str
    message: str


class MeetingRepository:
    """SQLite application data and deterministic backend permission checks."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.setup()

    def close(self) -> None:
        self.conn.close()

    def setup(self) -> None:
        self.conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                meeting_date TEXT NOT NULL,
                creator_id TEXT NOT NULL REFERENCES users(id),
                summary TEXT NOT NULL,
                transcript TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meeting_access (
                meeting_id TEXT NOT NULL REFERENCES meetings(id),
                user_id TEXT NOT NULL REFERENCES users(id),
                role TEXT NOT NULL,
                PRIMARY KEY (meeting_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS recording_sessions (
                thread_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL REFERENCES users(id),
                recording_state TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS recording_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                command TEXT NOT NULL,
                previous_state TEXT NOT NULL,
                current_state TEXT NOT NULL,
                ok INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS thread_modal_status (
                thread_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def seed_dummy_data(self) -> None:
        users = [
            ("user-eric", "Eric"),
            ("user-alice", "Alice"),
            ("user-bob", "Bob"),
        ]
        meetings = [
            (
                "meeting-001",
                "ZEN AI 제품 로드맵 회의",
                "2026-08-20",
                "user-eric",
                "회의록 Agent POC와 Graph Engineering 적용 범위를 결정했다.",
                "결정 사항: 회의 녹화는 결정적 Workflow로 유지한다. 검색과 질문은 LangGraph Agent로 구현한다. SQLite POC를 먼저 만든다.",
            ),
            (
                "meeting-002",
                "3분기 마케팅 전략 회의",
                "2026-08-22",
                "user-alice",
                "ZEN AI 출시 채널과 콘텐츠 일정을 논의했다.",
                "결정 사항: 9월 첫째 주에 기술 블로그를 공개한다. 데모 영상은 제품팀과 마케팅팀이 공동 제작한다.",
            ),
            (
                "meeting-003",
                "보안 검토 회의",
                "2026-08-25",
                "user-bob",
                "회의록 원문과 권한 검증 방식을 검토했다.",
                "결정 사항: 권한은 LLM이 아니라 백엔드가 검사한다. 원문은 Model Context에만 넣고 Checkpoint에는 저장하지 않는다.",
            ),
            (
                "meeting-004",
                "재무 비공개 회의",
                "2026-08-27",
                "user-bob",
                "접근 제한된 재무 계획이다.",
                "비공개 결정 사항: 이 문서는 권한이 없는 사용자에게 절대 노출하면 안 된다.",
            ),
        ]
        access = [
            ("meeting-001", "user-eric", "creator"),
            ("meeting-001", "user-alice", "participant"),
            ("meeting-002", "user-alice", "creator"),
            ("meeting-002", "user-eric", "shared"),
            ("meeting-003", "user-bob", "creator"),
            ("meeting-003", "user-eric", "shared"),
            ("meeting-004", "user-bob", "creator"),
        ]
        self.conn.executemany("INSERT OR IGNORE INTO users(id, name) VALUES (?, ?)", users)
        self.conn.executemany(
            """INSERT OR IGNORE INTO meetings
               (id, title, meeting_date, creator_id, summary, transcript)
               VALUES (?, ?, ?, ?, ?, ?)""",
            meetings,
        )
        self.conn.executemany(
            "INSERT OR IGNORE INTO meeting_access(meeting_id, user_id, role) VALUES (?, ?, ?)",
            access,
        )
        self.conn.commit()

    def list_accessible_meetings(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT m.id, m.title, m.meeting_date, m.summary, a.role
            FROM meetings m
            JOIN meeting_access a ON a.meeting_id = m.id
            WHERE a.user_id = ?
            ORDER BY m.meeting_date DESC, m.id
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def search_meetings(self, user_id: str, query: SearchQuery) -> list[dict[str, Any]]:
        accessible = self.list_accessible_meetings(user_id)
        explicit_ids = {item.lower() for item in query["meeting_ids"]}
        if explicit_ids:
            return [item for item in accessible if item["id"].lower() in explicit_ids]

        meeting_date = query["meeting_date"]
        if meeting_date:
            accessible = [item for item in accessible if item["meeting_date"] == meeting_date]

        tokens = [token.lower() for token in query["keywords"]]
        if not tokens:
            return accessible

        scored: list[tuple[int, dict[str, Any]]] = []
        for item in accessible:
            haystack = f"{item['title']} {item['summary']} {item['meeting_date']}".lower()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["meeting_date"], pair[1]["id"]))
        return [item for _, item in scored]

    def validate_selection(
        self,
        user_id: str,
        candidate_ids: Iterable[str],
        selected_ids: Iterable[str],
    ) -> list[str]:
        candidate_set = set(candidate_ids)
        selected = list(dict.fromkeys(selected_ids))
        if not selected or any(item not in candidate_set for item in selected):
            raise ValueError("선택 ID는 검색 후보 집합 안에서 하나 이상 지정해야 합니다.")
        allowed = {item["id"] for item in self.list_accessible_meetings(user_id)}
        if any(item not in allowed for item in selected):
            raise PermissionError("백엔드 재검증에서 접근 권한이 없는 ID가 발견되었습니다.")
        return selected

    def get_meeting_documents(
        self, user_id: str, authorized_ids: Iterable[str]
    ) -> list[dict[str, str]]:
        ids = list(dict.fromkeys(authorized_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"""
            SELECT m.id, m.title, m.meeting_date, m.summary, m.transcript
            FROM meetings m
            JOIN meeting_access a ON a.meeting_id = m.id
            WHERE a.user_id = ? AND m.id IN ({placeholders})
            ORDER BY m.meeting_date, m.id
            """,
            (user_id, *ids),
        ).fetchall()
        found = {row["id"] for row in rows}
        if found != set(ids):
            missing = sorted(set(ids) - found)
            raise PermissionError(f"UNAUTHORIZED_MEETING_ID: {', '.join(missing)}")
        return [dict(row) for row in rows]

    def get_modal_status(self, thread_id: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM thread_modal_status WHERE thread_id = ?", (thread_id,)
        ).fetchone()
        return row["status"] if row else "healthy"

    def get_recording_state(self, thread_id: str) -> str:
        row = self.conn.execute(
            "SELECT recording_state FROM recording_sessions WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        return row["recording_state"] if row else "none"

    def execute_recording_command(
        self, user_id: str, thread_id: str, command: str
    ) -> RecordingResult:
        now = datetime.now(timezone.utc).isoformat()
        row = self.conn.execute(
            "SELECT owner_id, recording_state FROM recording_sessions WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
        previous = row["recording_state"] if row else "none"
        owner = row["owner_id"] if row else user_id

        transitions = {
            ("none", "start"): "recording",
            ("recording", "pause"): "pause",
            ("pause", "resume"): "recording",
            ("recording", "stop"): "none",
            ("pause", "stop"): "none",
        }
        if owner != user_id:
            return self._record_rejection(
                thread_id, user_id, command, previous, "생성자만 녹화를 제어할 수 있습니다.", now
            )
        current = transitions.get((previous, command))
        if current is None:
            return self._record_rejection(
                thread_id,
                user_id,
                command,
                previous,
                f"recording_state={previous}에서는 {command} 명령을 실행할 수 없습니다.",
                now,
            )

        self.conn.execute(
            """
            INSERT INTO recording_sessions(thread_id, owner_id, recording_state, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET
                recording_state=excluded.recording_state,
                updated_at=excluded.updated_at
            """,
            (thread_id, user_id, current, now),
        )
        self.conn.execute(
            "INSERT INTO recording_events(thread_id, user_id, command, previous_state, current_state, ok, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (thread_id, user_id, command, previous, current, now),
        )
        self.set_modal_status(thread_id, "healthy", now)
        self.conn.commit()
        transition = f"{previous} → {'stop → none' if command == 'stop' else current}"
        return RecordingResult(True, previous, current, "healthy", f"녹화 명령 성공: {transition}")

    def _record_rejection(
        self,
        thread_id: str,
        user_id: str,
        command: str,
        previous: str,
        message: str,
        now: str,
    ) -> RecordingResult:
        self.conn.execute(
            "INSERT INTO recording_events(thread_id, user_id, command, previous_state, current_state, ok, created_at) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (thread_id, user_id, command, previous, previous, now),
        )
        # A normal state/permission rejection is not a modal execution error.
        self.set_modal_status(thread_id, "healthy", now)
        self.conn.commit()
        return RecordingResult(False, previous, previous, "healthy", message)

    def set_modal_status(
        self, thread_id: str, status: str, now: str | None = None
    ) -> None:
        now = now or datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT INTO thread_modal_status(thread_id, status, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(thread_id) DO UPDATE SET status=excluded.status, updated_at=excluded.updated_at
            """,
            (thread_id, status, now),
        )
        self.conn.commit()
