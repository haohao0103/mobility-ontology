"""画像语义层写操作门禁：提议、人工确认、审计留痕。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(ROOT, "runs", "profile_audit.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditStore:
    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv("PROFILE_AUDIT_DB", DEFAULT_DB)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS proposals (
                id TEXT PRIMARY KEY,
                action TEXT NOT NULL,
                asset_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                reason TEXT NOT NULL,
                actor TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                confirmed_by TEXT,
                confirmed_at TEXT,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                proposal_id TEXT,
                action TEXT NOT NULL,
                asset_id TEXT,
                actor TEXT NOT NULL,
                detail TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_proposals_status
                ON proposals(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_audit_created
                ON audit_log(created_at);
            """)

    def create_proposal(self, action: str, asset_id: str, payload: dict,
                        reason: str, actor: str) -> dict:
        pid = "prop_" + uuid.uuid4().hex[:16]
        ts = now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO proposals(id,action,asset_id,payload,reason,actor,"
                "status,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (pid, action, asset_id, json.dumps(payload, ensure_ascii=False),
                 reason, actor, "PENDING", ts))
            conn.execute(
                "INSERT INTO audit_log(proposal_id,action,asset_id,actor,detail,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (pid, "propose_" + action, asset_id, actor,
                 json.dumps({"reason": reason, "payload": payload},
                            ensure_ascii=False), ts))
        return self.get_proposal(pid)  # type: ignore[return-value]

    def get_proposal(self, proposal_id: str) -> Optional[dict]:
        with self._lock, self._conn() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id=?",
                               (proposal_id,)).fetchone()
        return self._proposal_row(row) if row else None

    def list_proposals(self, status: Optional[str] = None,
                       limit: int = 50) -> List[dict]:
        sql = "SELECT * FROM proposals"
        args: list = []
        if status:
            sql += " WHERE status=?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock, self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [self._proposal_row(r) for r in rows]

    def finish_proposal(self, proposal_id: str, status: str, approver: str,
                        result: Any) -> dict:
        ts = now_iso()
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE proposals SET status=?,confirmed_by=?,confirmed_at=?,"
                "result=? WHERE id=?",
                (status, approver, ts,
                 json.dumps(result, ensure_ascii=False, default=str), proposal_id))
            conn.execute(
                "INSERT INTO audit_log(proposal_id,action,asset_id,actor,detail,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (proposal_id, "confirm_" + status,
                 result.get("asset_id") if isinstance(result, dict) else None,
                 approver,
                 json.dumps({"result": result}, ensure_ascii=False, default=str), ts))
        return self.get_proposal(proposal_id)  # type: ignore[return-value]

    def audit(self, proposal_id: Optional[str], action: str, asset_id: Optional[str],
              actor: str, detail: dict) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO audit_log(proposal_id,action,asset_id,actor,detail,"
                "created_at) VALUES(?,?,?,?,?,?)",
                (proposal_id, action, asset_id, actor,
                 json.dumps(detail, ensure_ascii=False, default=str), now_iso()))

    def list_audit(self, limit: int = 100) -> List[dict]:
        with self._lock, self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{
            "id": r["id"], "proposal_id": r["proposal_id"], "action": r["action"],
            "asset_id": r["asset_id"], "actor": r["actor"],
            "detail": self._loads(r["detail"]), "created_at": r["created_at"],
        } for r in rows]

    @staticmethod
    def _loads(value: Optional[str]) -> Any:
        if not value:
            return None
        try:
            return json.loads(value)
        except Exception:
            return value

    def _proposal_row(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"], "action": row["action"], "asset_id": row["asset_id"],
            "payload": self._loads(row["payload"]), "reason": row["reason"],
            "actor": row["actor"], "status": row["status"],
            "created_at": row["created_at"], "confirmed_by": row["confirmed_by"],
            "confirmed_at": row["confirmed_at"],
            "result": self._loads(row["result"]),
        }