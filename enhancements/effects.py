"""Action 副作用：webhook / 通知 / 触发器（P-07 补齐）。

声明来自本体扩展 JSON 的顶层键 `action_effects`（承重：改 JSON 即可挂/摘副作用），
运行时在动作成功提交后派发。POC 的 webhook 不真发 HTTP——写入 effect_log
作为可审计的投递凭据；真实网关接入点留在 _deliver_webhook。

触发器（trigger）会在同一运行时级联执行另一个动作，带深度上限防环。
"""
from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS effect_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  action_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  effect_type TEXT NOT NULL,
  target TEXT NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class EffectError(Exception):
    pass


class EffectDispatcher:
    def __init__(self, ext: Any, system: Any, conn: Any):
        self.ext = ext
        self.system = system
        self.conn = conn
        conn.executescript(SCHEMA)

    def declarations(self, action_id: str | None = None) -> list[dict]:
        raw = getattr(self.ext, "raw", {}) or {}
        effects = raw.get("action_effects", {})
        if not isinstance(effects, dict):
            return []
        if action_id:
            return effects.get(action_id, [])
        return [{"action_id": k, **e} for k, v in effects.items() for e in v]

    def _log(self, action_id: str, object_id: str, effect_type: str,
             target: str, payload: dict, status: str, error: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO effect_log(occurred_at,action_id,object_id,effect_type,target,"
            "payload,status,error) VALUES (?,?,?,?,?,?,?,?)",
            (_now(), action_id, object_id, effect_type, target,
             json.dumps(payload, ensure_ascii=False, default=str), status, error))
        self.conn.commit()

    def dispatch(self, action_id: str, object_id: str, result: dict,
                 actor: str, role: str, _depth: int = 0) -> list[dict]:
        if _depth > 3:
            raise EffectError("副作用触发链超过 3 层，疑似循环")
        out = []
        for spec in self.declarations(action_id):
            etype = spec.get("type")
            try:
                if etype == "webhook":
                    self._deliver_webhook(spec, action_id, object_id, result)
                    self._log(action_id, object_id, etype,
                              spec.get("url", ""), result, "DELIVERED")
                elif etype == "notification":
                    self._notify(spec, action_id, object_id, result)
                    self._log(action_id, object_id, etype,
                              ",".join(spec.get("channels", [])), result, "NOTIFIED")
                elif etype == "trigger":
                    target = spec.get("action_id")
                    if not target:
                        raise EffectError("trigger 缺少 action_id")
                    self.system.execute_action(
                        target, spec.get("object_id") or object_id, actor, role,
                        spec.get("payload") or {}, spec.get("idempotency_key"),
                        defer_commit=False)
                    self._log(action_id, object_id, etype, target, result, "TRIGGERED")
                    out += self.dispatch(target, spec.get("object_id") or object_id,
                                         {}, actor, role, _depth + 1)
                else:
                    raise EffectError(f"未知副作用类型: {etype}")
                out.append({"action_id": action_id, "type": etype, "ok": True})
            except Exception as exc:  # noqa: BLE001 - 副作用失败不影响已提交动作
                self._log(action_id, object_id, etype or "?", "", result, "FAILED",
                          f"{type(exc).__name__}: {exc}")
                out.append({"action_id": action_id, "type": etype, "ok": False,
                            "error": str(exc)})
        return out

    def _deliver_webhook(self, spec: dict, action_id: str, object_id: str, result: dict) -> None:
        """真实投递接入点。POC 只记录，不发起网络请求（禁本地起服务）。"""
        if not spec.get("url"):
            raise EffectError("webhook 缺少 url")

    def _notify(self, spec: dict, action_id: str, object_id: str, result: dict) -> None:
        if not spec.get("channels"):
            raise EffectError("notification 缺少 channels")

    def log(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT occurred_at,action_id,object_id,effect_type,target,status,error "
            "FROM effect_log ORDER BY id DESC LIMIT ?", (limit,))]
