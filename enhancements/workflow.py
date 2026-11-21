"""Worksheet / Workflow 运行时（对标 Palantir Workshop / Workflows 的本体内核）。

声明来自本体扩展 JSON 顶层键（承重：改 JSON 即可建表/改流程，代码零改动）：
  "worksheets": [{"id","label","objects":[...],"view":"table|graph","columns":[...]}]
  "workflows":  [{"id","label","steps":[
      {"id","type":"action|query|approval","action_id","object_id","role",
       "payload","tool","arguments","condition","approver"}]}]

运行态落 SQLite：每次 run 记一行 + 每步记一行；approval 步骤会暂停并返回
awaiting_approval，由人工确认后从断点继续（对应 Workshop 的人工节点）。
"""
from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
  run_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL,
  status TEXT NOT NULL,
  context TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_steps (
  run_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  step_id TEXT NOT NULL,
  step_type TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  PRIMARY KEY (run_id, seq)
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class WorkflowError(Exception):
    pass


class WorkflowEngine:
    def __init__(self, ext: Any, system: Any, conn: Any):
        self.ext = ext
        self.system = system
        self.conn = conn
        conn.executescript(SCHEMA)

    def _raw(self) -> dict:
        return getattr(self.ext, "raw", {}) or {}

    def worksheets(self) -> list[dict]:
        return self._raw().get("worksheets", []) or []

    def worksheet(self, wid: str) -> dict:
        for ws in self.worksheets():
            if ws.get("id") == wid:
                return ws
        raise WorkflowError(f"Worksheet 不存在: {wid}")

    def render_worksheet(self, wid: str) -> dict:
        ws = self.worksheet(wid)
        objects = []
        for oid in ws.get("objects", []):
            row = self.system.facts.get_row(oid)
            if row is None:
                continue
            props = json.loads(row["props"])
            cols = ws.get("columns")
            objects.append({
                "object_id": oid, "type": row["type"], "name": row["name"],
                "status": row["status"],
                "props": {k: props.get(k) for k in cols} if cols else props,
            })
        return {"id": wid, "label": ws.get("label"), "view": ws.get("view", "table"),
                "objects": objects}

    def workflows(self) -> list[dict]:
        return [{"id": w.get("id"), "label": w.get("label"),
                 "steps": len(w.get("steps", []))} for w in self._raw().get("workflows", [])]

    def _workflow(self, wid: str) -> dict:
        for w in self._raw().get("workflows", []):
            if w.get("id") == wid:
                return w
        raise WorkflowError(f"Workflow 不存在: {wid}")

    def start(self, workflow_id: str, run_id: str, context: dict | None = None) -> dict:
        wf = self._workflow(workflow_id)
        ctx = context or {}
        ctx.setdefault("workflow_id", workflow_id)
        now = _now()
        self.conn.execute(
            "INSERT OR REPLACE INTO workflow_runs(run_id,workflow_id,status,context,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (run_id, workflow_id, "RUNNING", json.dumps(ctx, ensure_ascii=False), now, now))
        self.conn.commit()
        return self.advance(run_id)

    def advance(self, run_id: str) -> dict:
        run = self.conn.execute("SELECT * FROM workflow_runs WHERE run_id=?",
                                (run_id,)).fetchone()
        if run is None:
            raise WorkflowError(f"运行不存在: {run_id}")
        if run["status"] in ("COMPLETED", "FAILED"):
            return dict(run)
        wf = self._workflow(run["workflow_id"])
        ctx = json.loads(run["context"])
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq),-1)+1 FROM workflow_steps WHERE run_id=?",
            (run_id,)).fetchone()[0]
        steps = wf.get("steps", [])
        if seq >= len(steps):
            self.conn.execute(
                "UPDATE workflow_runs SET status='COMPLETED', updated_at=? WHERE run_id=?",
                (_now(), run_id))
            self.conn.commit()
            return {"run_id": run_id, "status": "COMPLETED"}

        step = steps[seq]
        stype = step.get("type")
        status, detail = "DONE", {}
        try:
            if stype == "action":
                detail = self.system.execute_action(
                    step["action_id"], step.get("object_id") or ctx.get("object_id"),
                    ctx.get("actor", "workflow"), step.get("role", ctx.get("role", "operator")),
                    step.get("payload") or {})
            elif stype == "query":
                detail = self.system.mcp_call(step.get("tool"), step.get("arguments") or {})
                ctx[f"step_{seq}_result"] = detail
            elif stype == "approval":
                status = "AWAITING_APPROVAL"
                detail = {"step": step.get("id"), "approver": step.get("approver")}
            elif stype == "edit":
                self.system.facts.update_object(
                    step["object_id"], step.get("set") or {}, None, None, _now())
                detail = {"object_id": step["object_id"]}
            else:
                raise WorkflowError(f"未知步骤类型: {stype}")
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAILED", {"error": f"{type(exc).__name__}: {exc}"}

        self.conn.execute(
            "INSERT INTO workflow_steps(run_id,seq,step_id,step_type,status,detail) "
            "VALUES (?,?,?,?,?,?)",
            (run_id, seq, step.get("id", f"s{seq}"), stype, status,
             json.dumps(detail, ensure_ascii=False, default=str)))
        self.conn.execute(
            "UPDATE workflow_runs SET status=?, context=?, updated_at=? WHERE run_id=?",
            ("WAITING" if status == "AWAITING_APPROVAL" else
             "FAILED" if status == "FAILED" else "RUNNING",
             json.dumps(ctx, ensure_ascii=False), _now(), run_id))
        self.conn.commit()
        if status == "DONE" or status == "FAILED":
            return self.advance(run_id) if status == "DONE" else \
                {"run_id": run_id, "status": "FAILED", "detail": detail}
        return {"run_id": run_id, "status": "WAITING", "detail": detail}

    def approve(self, run_id: str, approver: str) -> dict:
        run = self.conn.execute("SELECT status FROM workflow_runs WHERE run_id=?",
                                (run_id,)).fetchone()
        if run is None or run["status"] != "WAITING":
            raise WorkflowError(f"运行 {run_id} 不在等待审批状态")
        last = self.conn.execute(
            "SELECT seq FROM workflow_steps WHERE run_id=? AND status='AWAITING_APPROVAL' "
            "ORDER BY seq DESC LIMIT 1", (run_id,)).fetchone()
        if last is None:
            raise WorkflowError("找不到待审批步骤")
        self.conn.execute(
            "UPDATE workflow_steps SET status='APPROVED', detail=json_set(detail,'$.approver',?) "
            "WHERE run_id=? AND seq=?", (approver, run_id, last["seq"]))
        self.conn.execute("UPDATE workflow_runs SET status='RUNNING' WHERE run_id=?",
                          (run_id,))
        self.conn.commit()
        return self.advance(run_id)

    def runs(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT run_id,workflow_id,status,created_at,updated_at FROM workflow_runs "
            "ORDER BY created_at DESC LIMIT ?", (limit,))]

    def run_detail(self, run_id: str) -> dict:
        run = self.conn.execute("SELECT * FROM workflow_runs WHERE run_id=?",
                                (run_id,)).fetchone()
        if run is None:
            raise WorkflowError(f"运行不存在: {run_id}")
        steps = [dict(s) for s in self.conn.execute(
            "SELECT seq,step_id,step_type,status,detail FROM workflow_steps "
            "WHERE run_id=? ORDER BY seq", (run_id,))]
        return {**dict(run), "steps": steps}
