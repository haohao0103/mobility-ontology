"""授权引擎骨架：策略引擎 + OpenFGA / OPA / Cedar 适配器（P-09 平台件补齐）。

现有 SecurityEngine 负责 marking 传播与列脱敏（本体语义）；本模块在其上提供
**可替换的授权引擎接口**，让 ReBAC/ABAC 骨架可以按需接入而不改业务代码。

POC 纪律：
- 内置 policy 引擎直接委托 SecurityEngine（真实可用）；
- OpenFGA / OPA / Cedar 适配器只做协议骨架与配置校验，不发起网络请求
  （禁止本地起服务；真实网关由部署方配置 endpoint 后启用）。
- 任何引擎的检查结果都落审计，便于对照。
"""
from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS authz_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  occurred_at TEXT NOT NULL,
  engine TEXT NOT NULL,
  subject TEXT NOT NULL,
  action TEXT NOT NULL,
  resource TEXT NOT NULL,
  decision TEXT NOT NULL,
  detail TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AuthzEngine:
    kind = "base"

    def check(self, subject: dict, action: str, resource: dict) -> dict:
        raise NotImplementedError


class PolicyAuthz(AuthzEngine):
    """内置策略引擎：委托本体扩展的 marking / 行列策略。"""

    kind = "policy"

    def __init__(self, security: Any):
        self.security = security

    def check(self, subject: dict, action: str, resource: dict) -> dict:
        from enhancements.security import SecurityContext

        ctx = SecurityContext(
            user=subject.get("user", "anonymous"),
            roles=set(subject.get("roles", [])),
            markings=set(subject.get("markings", subject.get("roles", []))),
            scope_token=subject.get("scope_token"),
        )
        cls = resource.get("type", "")
        props = resource.get("props", {})
        if resource.get("requires_marking") \
                and resource["requires_marking"] not in ctx.markings:
            return {"allow": False, "reason": "MARKING_REQUIRED"}
        if action in ("read", "query") and not self.security.visible_object(cls, props, ctx):
            return {"allow": False, "reason": "ROW_LEVEL_DENIED"}
        return {"allow": True, "reason": "POLICY_ALLOWED"}


class _RemoteAdapter(AuthzEngine):
    """远程授权引擎骨架：只校验配置与生成请求体，不实际发请求。"""

    required: tuple[str, ...] = ()

    def __init__(self, config: dict):
        self.config = config
        self.missing = [k for k in self.required if not config.get(k)]

    def check(self, subject: dict, action: str, resource: dict) -> dict:
        if self.missing:
            return {"allow": False, "reason": "ENGINE_NOT_CONFIGURED",
                    "missing": self.missing}
        payload = {"subject": subject, "action": action, "resource": resource}
        return {"allow": False, "reason": "ENGINE_CALL_NOT_ENABLED",
                "request": payload, "endpoint": self.config.get("endpoint")}


class OpenFGAAuthz(_RemoteAdapter):
    kind = "openfga"
    required = ("endpoint", "store_id", "model_id")


class OPAAuthz(_RemoteAdapter):
    kind = "opa"
    required = ("endpoint", "policy_path")


class CedarAuthz(_RemoteAdapter):
    kind = "cedar"
    required = ("endpoint", "policy_set")


class AuthzRouter:
    def __init__(self, security: Any, conn: Any, config: dict | None = None):
        cfg = config or {}
        self.engines: dict[str, AuthzEngine] = {
            "policy": PolicyAuthz(security),
            "openfga": OpenFGAAuthz(cfg.get("openfga", {})),
            "opa": OPAAuthz(cfg.get("opa", {})),
            "cedar": CedarAuthz(cfg.get("cedar", {})),
        }
        self.conn = conn
        conn.executescript(SCHEMA)

    def check(self, engine: str, subject: dict, action: str, resource: dict) -> dict:
        impl = self.engines.get(engine)
        if impl is None:
            raise ValueError(f"未知授权引擎: {engine}")
        result = impl.check(subject, action, resource)
        decision = "ALLOW" if result.get("allow") else "DENY"
        self.conn.execute(
            "INSERT INTO authz_checks(occurred_at,engine,subject,action,resource,decision,detail) "
            "VALUES (?,?,?,?,?,?,?)",
            (_now(), engine, json.dumps(subject, ensure_ascii=False), action,
             json.dumps(resource, ensure_ascii=False), decision,
             json.dumps(result, ensure_ascii=False, default=str)))
        self.conn.commit()
        return {"engine": engine, "decision": decision, **result}

    def catalog(self) -> list[dict]:
        return [{"kind": k, "configured": not getattr(v, "missing", []),
                 "missing": getattr(v, "missing", [])}
                for k, v in self.engines.items()]

    def audit(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT occurred_at,engine,subject,action,resource,decision FROM authz_checks "
            "ORDER BY id DESC LIMIT ?", (limit,))]
