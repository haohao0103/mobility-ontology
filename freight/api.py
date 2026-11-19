"""货拉拉订单本体 REST 接口：/freight/*"""
from __future__ import annotations

import os
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .agent import LLMError
from .engine import FreightSystem
from .semantic import BusinessReject, OntologyViolation

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 外部业务事实的演示替身；本体知识在 freight/semantic_layer.json。
DEFAULT_FACT_DB = os.path.join(ROOT, "runs", "freight_facts.db")
_LOCK = threading.Lock()
_SYSTEM: FreightSystem | None = None


def get_system() -> FreightSystem:
    global _SYSTEM
    if _SYSTEM is None:
        _SYSTEM = FreightSystem(DEFAULT_FACT_DB, fresh=False, seed=True)
    return _SYSTEM


class ActionIn(BaseModel):
    object_id: str
    actor: str
    role: str
    payload: dict = {}
    idempotency_key: Optional[str] = None


class MCPCallIn(BaseModel):
    tool: str
    arguments: dict = {}


class VersionIn(BaseModel):
    version: str


router = APIRouter(prefix="/freight", tags=["freight-ontology"])


def guard(fn):
    with _LOCK:
        return translate(fn)


def agent_guard(fn):
    # Agent 会多次调用大模型与 MCP，不能长时间占用全局锁，避免阻塞其他接口。
    return translate(fn)


def translate(fn):
    try:
        return fn()
    except LLMError as e:
        raise HTTPException(status_code=503, detail={
            "code": "LLM_UNAVAILABLE", "message": str(e),
        })
    except BusinessReject as e:
        status = 404 if e.code in ("OBJECT_NOT_FOUND", "VERSION_NOT_FOUND") else (
            403 if e.code == "FORBIDDEN_ROLE" else 409
        )
        raise HTTPException(status_code=status, detail={
            "code": e.code, "message": str(e), "details": e.details,
        })
    except OntologyViolation as e:
        raise HTTPException(status_code=422, detail={
            "code": "ONTOLOGY_VIOLATION", "message": str(e),
        })
    except ValueError as e:
        raise HTTPException(status_code=422, detail={
            "code": "VALIDATION_ERROR", "message": str(e),
        })


@router.get("/health")
def health():
    def fn():
        sys = get_system()
        return {
            "ok": True,
            "knowledge": {
                "source": "freight/semantic_layer.json",
                "version": sys.active_version,
                "classes": len(sys.sem.classes),
                "relations": len(sys.sem.relations),
                "rules": len(sys.sem.rules),
                "actions": len(sys.sem.actions),
            },
            "facts": {
                "transport": "MCP / API",
                "persisted_by_ontology": False,
                "demo_adapter_db": DEFAULT_FACT_DB,
                "stats": sys.stats(),
            },
        }
    return guard(fn)


@router.get("/storage")
def storage():
    return guard(lambda: get_system().storage_inventory())


@router.get("/meta")
def meta():
    def fn():
        sys = get_system()
        return {
            **sys.sem.dictionary(),
            "active_version": sys.active_version,
            "storage": {
                "knowledge": sys.sem.path,
                "facts": "MCP / API 按需查询，不写入本体库",
            },
            "knowledge_stats": {
                "classes": len(sys.sem.classes),
                "relations": len(sys.sem.relations),
                "rules": len(sys.sem.rules),
                "actions": len(sys.sem.actions),
            },
            "demo": {
                "shipper": "shipper:S001",
                "order": "order:HL20261105001",
                "waybill": "waybill:WB20261105001",
                "driver": "driver:D001",
                "vehicle": "vehicle:V001",
                "fee": "fee:F003",
                "settlement": "settlement:ST20261105001",
                "invoice": "invoice:INV20261105001",
                "exception": "exception:EX20261105001",
                "reassign_allowed": "waybill:WB20261106001",
                "reassign_blocked": "waybill:WB20261106002",
            },
        }
    return guard(fn)


@router.get("/ontology")
def ontology():
    return guard(lambda: get_system().ontology())


@router.get("/ontology/versions")
def ontology_versions():
    return guard(lambda: get_system().semantic_versions())


@router.get("/ontology/diff")
def ontology_diff(from_version: Optional[str] = Query(None),
                  to_version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_diff(from_version, to_version))


@router.get("/ontology/impact")
def ontology_impact(from_version: Optional[str] = Query(None),
                    to_version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_impact(from_version, to_version))


@router.get("/ontology/validate")
def ontology_validate(version: Optional[str] = Query(None)):
    return guard(lambda: get_system().semantic_validate(version))


@router.get("/ontology/guard-demo")
def ontology_guard_demo():
    return guard(lambda: get_system().semantic_guard_demo())


@router.post("/ontology/activate")
def ontology_activate(body: VersionIn):
    return guard(lambda: get_system().activate_version(body.version))


@router.post("/ontology/rollback")
def ontology_rollback():
    return guard(lambda: get_system().rollback_version())


@router.get("/facts/overview")
def facts_overview():
    return guard(lambda: get_system().overview())


@router.get("/facts/query/{waybill_id}")
def fact_query(waybill_id: str, include_context: bool = False):
    return guard(lambda: get_system().query_waybill(waybill_id, include_context))


@router.get("/mcp/tools")
def mcp_tools(type: Optional[str] = Query(default=None)):
    return guard(lambda: get_system().mcp_tools(type))


@router.post("/mcp/call")
def mcp_call(body: MCPCallIn):
    return guard(lambda: get_system().mcp_call(body.tool, body.arguments))


@router.get("/objects")
def objects(type: Optional[str] = None, q: Optional[str] = None,
            limit: int = Query(100, ge=1, le=500)):
    return guard(lambda: {"objects": get_system().search(type, q, limit)})


@router.get("/objects/{object_id}")
def object_detail(object_id: str):
    return guard(lambda: get_system().get_object(object_id))


@router.get("/objects/{object_id}/graph")
def object_graph(object_id: str, depth: int = Query(2, ge=1, le=5)):
    return guard(lambda: get_system().graph(object_id, depth))


@router.get("/objects/{object_id}/trace")
def object_trace(object_id: str):
    return guard(lambda: get_system().trace(object_id))


@router.get("/objects/{object_id}/impact")
def object_impact(object_id: str):
    return guard(lambda: get_system().impact(object_id))


@router.get("/waybills/{waybill_id}/fee-analysis")
def fee_analysis(waybill_id: str, version: Optional[str] = Query(None)):
    def fn():
        sys = get_system()
        sem = sys.semantic_for(version) if version else None
        return sys.fee_analysis(waybill_id, sem=sem)
    return guard(fn)


@router.get("/waybills/{waybill_id}/explain")
def waybill_explain(waybill_id: str,
                    audience: str = Query("cs", pattern="^(cs|finance|driver)$"),
                    version: Optional[str] = Query(None)):
    def fn():
        sys = get_system()
        sem = sys.semantic_for(version) if version else None
        return sys.explain(waybill_id, audience=audience, sem=sem)
    return guard(fn)


@router.post("/waybills/{waybill_id}/reasoning-chain/start")
def reasoning_chain_start(waybill_id: str,
                          max_steps: int = Query(6, ge=2, le=10),
                          question: str = Query("", max_length=300)):
    return agent_guard(lambda: get_system().agent_chain_start(
        waybill_id, max_steps=max_steps, question=question,
    ))


@router.post("/reasoning-chain/{session_id}/next")
def reasoning_chain_next(session_id: str):
    return agent_guard(lambda: get_system().agent_chain_next(session_id))


@router.get("/waybills/{waybill_id}/reasoning-chain")
def reasoning_chain(waybill_id: str,
                    max_steps: int = Query(6, ge=2, le=10),
                    question: str = Query("", max_length=300)):
    return agent_guard(lambda: get_system().reasoning_chain(
        waybill_id, max_steps=max_steps, question=question,
    ))


@router.get("/audit")
def audit(object_id: Optional[str] = None,
          limit: int = Query(100, ge=1, le=500)):
    return guard(lambda: {"audit": get_system().audit(object_id, limit)})


@router.post("/actions/{action_id}")
def run_action(action_id: str, body: ActionIn):
    return guard(lambda: get_system().execute_action(
        action_id=action_id,
        object_id=body.object_id,
        actor=body.actor,
        role=body.role,
        payload=body.payload,
        idempotency_key=body.idempotency_key,
    ))


# -------------------------------------------------- 组合式 Object Set 查询（E4）
# 对标 Palantir OSDK 组合语义：谓词/集合代数/followLink/聚合，安全联动（行级过滤影响
# total 与聚合，列级脱敏作用于输出 props；scope token 检查集合涉及的主类型）。

class ObjectSetQueryIn(BaseModel):
    set: dict
    where: Optional[dict] = None
    orderBy: Optional[list] = None
    aggregate: Optional[dict] = None
    page: Optional[dict] = None


def _object_query_engine():
    from enhancements.runtime import EnhancedSystem
    from freight.object_query import ObjectQueryEngine
    enh = EnhancedSystem(get_system())
    return ObjectQueryEngine(get_system().sem, enh.ext, enh.security), enh


@router.post("/object-set/query")
def object_set_query(body: ObjectSetQueryIn,
                     user: str = Query("anonymous"), roles: str = Query("")):
    from enhancements.security import SecurityContext, AccessDenied
    from freight.object_query import QueryError
    from freight.semantic import OntologyViolation

    engine, enh = _object_query_engine()
    role_set = {r.strip() for r in roles.split(",") if r.strip()}
    token = enh.ext.scope_tokens.get("default_token")
    ctx = SecurityContext(
        user=user, roles=role_set, markings=role_set,
        scope_token=token if user.startswith("agent-") else None,
    )
    # scope token：集合主类型必须在令牌可见范围内
    try:
        if ctx.scope_token:
            main_type = engine.validate_set(body.set)
            if main_type == "*":
                raise AccessDenied(
                    "scope token 不允许代数组合查询（无法静态判定类型）",
                    code="TOOL_NOT_IN_SCOPE")
            engine.security.check_scope(ctx, "class", main_type)
        query = body.model_dump(exclude_none=True)
        return engine.execute(query, get_system().facts.store, ctx)
    except QueryError as exc:
        raise HTTPException(400, f"{exc.code}: {exc}") from exc
    except OntologyViolation as exc:
        raise HTTPException(400, f"ONTOLOGY_VIOLATION: {exc}") from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc