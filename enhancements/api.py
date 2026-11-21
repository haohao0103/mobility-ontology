"""增强层 REST 路由（挂 /enh/*）。

演示级安全上下文传递：?user=alice&roles=freight-core,PII
（生产部署应换为认证中间件注入，见 README 纪律节）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import APIRouter, HTTPException, Query  # noqa: E402
from pydantic import BaseModel  # noqa: E402
from typing import Any  # noqa: E402

from enhancements.runtime import EnhancedSystem  # noqa: E402
from enhancements.security import SecurityContext, AccessDenied  # noqa: E402
from enhancements.function_registry import FunctionRegistryError  # noqa: E402
from enhancements.property_registry import RegistryViolation  # noqa: E402
from enhancements.interfaces import InterfaceViolation  # noqa: E402
from enhancements import sandbox  # noqa: E402

router = APIRouter(prefix="/enh", tags=["enhancements"])

_SYSTEM: EnhancedSystem | None = None


def get_enh() -> EnhancedSystem:
    global _SYSTEM  # noqa: PLW0603
    if _SYSTEM is None:
        from freight.api import get_system
        _SYSTEM = EnhancedSystem(get_system())
    return _SYSTEM


def ctx_from(user: str, roles: str) -> SecurityContext:
    role_set = {r.strip() for r in roles.split(",") if r.strip()} if roles else set()
    token = get_enh().ext.scope_tokens.get("default_token")
    return SecurityContext(user=user, roles=role_set, markings=role_set,
                           scope_token=token if user.startswith("agent-") else None)


class SimulateIn(BaseModel):
    user: str = "anonymous"
    roles: list[str] = []
    class_id: str = "FeeItem"


class FunctionCallIn(BaseModel):
    args: dict = {}


@router.get("/health")
def health():
    e = get_enh()
    issues = e.ext.validate()
    return {
        "ok": not issues,
        "extension_version": e.ext.meta.get("version"),
        "property_types": len(e.ext.property_types),
        "interfaces": len(e.ext.interfaces),
        "markings": len(e.ext.markings),
        "object_security_policies": len(e.ext.object_security_policies),
        "property_security_policies": len(e.ext.property_security_policies),
        "functions_registered": len(e.ext.function_registry),
        "cross_reference_issues": issues,
    }


@router.get("/storage/v2")
def storage_v2():
    """OSv2 增强物化层：声明来自本体 JSON，含一致性凭据与查询计划统计。"""
    return get_enh().storage_v2_stats()


@router.post("/storage/v2/sync")
def storage_v2_sync():
    return get_enh().sync_materialization()


# -------------------------------------------------- 系统级血缘（P-10）
@router.get("/lineage/summary")
def lineage_summary():
    return get_enh().lineage.summary()


@router.get("/lineage/graph")
def lineage_graph():
    return get_enh().lineage.build()


@router.get("/lineage/property/{class_id}/{prop}")
def lineage_property(class_id: str, prop: str):
    return get_enh().lineage.trace_property(class_id, prop)


@router.get("/lineage/object/{object_id}")
def lineage_object(object_id: str):
    return get_enh().lineage.trace_object(object_id)


# -------------------------------------------------- 对象数据变更集与分支（P-03）
class ChangeSetIn(BaseModel):
    id: str
    label: str
    author: str = "system"


class ChangeItemIn(BaseModel):
    op: str
    object_id: str
    prop: str | None = None
    after: Any = None
    payload: dict = {}


class BranchIn(BaseModel):
    name: str
    note: str = ""


@router.get("/changesets")
def changesets():
    return {"changesets": get_enh().changes.list()}


@router.post("/changesets")
def changeset_create(body: ChangeSetIn):
    return get_enh().changes.create(body.id, body.label, body.author)


@router.post("/changesets/{change_id}/items")
def changeset_plan(change_id: str, body: ChangeItemIn):
    return get_enh().changes.plan(
        change_id, body.op, body.object_id, body.prop, body.after, body.payload)


@router.get("/changesets/{change_id}")
def changeset_preview(change_id: str):
    return get_enh().changes.preview(change_id)


@router.post("/changesets/{change_id}/apply")
def changeset_apply(change_id: str):
    return get_enh().changes.apply(change_id)


@router.post("/changesets/{change_id}/rollback")
def changeset_rollback(change_id: str):
    return get_enh().changes.rollback(change_id)


@router.get("/branches")
def branches():
    return {"branches": get_enh().changes.list_branches()}


@router.post("/branches")
def branch_create(body: BranchIn):
    return get_enh().changes.create_branch(body.name, body.note)


@router.post("/branches/{name}/restore")
def branch_restore(name: str):
    return get_enh().changes.restore_branch(name)


@router.get("/property-registry/{class_id}")
def property_registry(class_id: str):
    e = get_enh()
    try:
        props = e.properties.properties_of(class_id)
    except RegistryViolation as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "class": class_id,
        "policy": e.ext.policy,
        "properties": [
            {
                "name": p.name, "label": p.label, "value_type": p.value_type,
                "unit": p.unit, "marking": p.marking, "timeseries": p.timeseries,
                "primary_key": p.primary_key, "status": p.status,
                "registered": p.registered, "issues": p.issues,
            }
            for p in props
        ],
    }


@router.get("/interfaces")
def interfaces():
    e = get_enh()
    return {
        "interfaces": [
            {
                "id": i["id"], "label": i.get("label"),
                "match": i.get("match"),
                "implementations": e.interfaces.implementations(i["id"]),
            }
            for i in e.ext.interfaces.values()
        ]
    }


@router.get("/objects")
def objects(user: str = Query("anonymous"), roles: str = Query(""),
            type: str | None = None, q: str | None = None,
            limit: int = Query(20, le=100)):
    e = get_enh()
    ctx = ctx_from(user, roles)
    result = e.search(q=q, types=[type] if type else None, limit=limit)
    # 安全过滤 + 脱敏
    import json as _json
    rows = e.store.all(
        "SELECT * FROM objects WHERE object_id IN (%s)" % ",".join("?" * len(result["objects"])),
        tuple(o["object_id"] for o in result["objects"]),
    ) if result["objects"] else []
    out = []
    for r in rows:
        props = _json.loads(r["props"])
        if not e.security.visible_object(r["type"], props, ctx):
            continue
        masked = e.security.mask_properties(r["type"], props, ctx)
        out.append({
            "object_id": r["object_id"], "type": r["type"],
            "name": r["name"], "status": r["status"], "props": masked,
        })
    return {"visible": len(out), "user": user, "objects": out}


@router.get("/objects/{object_id}")
def secure_object(object_id: str, user: str = Query("anonymous"),
                  roles: str = Query("")):
    e = get_enh()
    ctx = ctx_from(user, roles)
    try:
        return e.secure_get_object(object_id, ctx)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc


@router.post("/security/simulate")
def security_simulate(body: SimulateIn):
    e = get_enh()
    import json as _json
    ctx = SecurityContext.with_roles(body.user, set(body.roles))
    rows = e.store.all("SELECT * FROM objects WHERE type=?", (body.class_id,))
    parsed = [
        {
            "object_id": r["object_id"], "status": r["status"],
            "props": _json.loads(r["props"]),
        }
        for r in rows
    ]
    return e.security.simulate(body.class_id, parsed, ctx)


@router.get("/functions")
def functions():
    return {"functions": get_enh().functions.list_functions()}


@router.post("/functions/{function_id}/call")
def function_call(function_id: str, body: FunctionCallIn,
                  user: str = Query("agent-readonly"), roles: str = Query("freight-core")):
    e = get_enh()
    ctx = ctx_from(user, roles)
    try:
        return e.call_function(function_id, body.args, ctx)
    except FunctionRegistryError as exc:
        code = exc.code
        status = 404 if code == "FUNCTION_UNKNOWN" else 400
        raise HTTPException(status, f"{code}: {exc}") from exc
    except InterfaceViolation as exc:
        raise HTTPException(400, f"INTERFACE_VIOLATION: {exc}") from exc
    except AccessDenied as exc:
        raise HTTPException(403, f"{exc.code}: {exc}") from exc


@router.post("/functions/{function_id}/sandbox")
def function_sandbox(function_id: str, body: FunctionCallIn):
    e = get_enh()
    fn = e.ext.function_registry.get(function_id)
    if fn is None:
        raise HTTPException(404, f"FUNCTION_UNKNOWN: {function_id}")
    cfg = fn.get("sandbox", {"timeout_ms": 5000, "memory_mb": 256})
    try:
        result = sandbox.run_in_sandbox(
            function_id, body.args, e.ext,
            timeout_ms=cfg.get("timeout_ms", 5000),
            memory_mb=cfg.get("memory_mb", 256),
        )
    except sandbox.SandboxTimeout as exc:
        raise HTTPException(504, f"SANDBOX_TIMEOUT: {exc}") from exc
    except sandbox.SandboxError as exc:
        raise HTTPException(500, f"SANDBOX_ERROR: {exc}") from exc
    return result


@router.get("/usage/summary")
def usage_summary():
    return get_enh().usage.usage_summary()


@router.get("/usage/{class_id}/dependents")
def usage_dependents(class_id: str):
    e = get_enh()
    deps = e.usage.dependents(class_id)
    return {
        "class": class_id,
        "dependents": deps,
        "deletable": not deps,
    }


# -------------------------------------------------- Worksheet / Workflow（Workshop 内核）
@router.get("/worksheets")
def worksheets():
    return {"worksheets": get_enh().workflow.worksheets()}


@router.get("/worksheets/{wid}")
def worksheet_render(wid: str):
    return get_enh().workflow.render_worksheet(wid)


@router.get("/workflows")
def workflows():
    return {"workflows": get_enh().workflow.workflows()}


class WorkflowStartIn(BaseModel):
    run_id: str
    context: dict = {}


class ApprovalIn(BaseModel):
    approver: str


@router.post("/workflows/{workflow_id}/start")
def workflow_start(workflow_id: str, body: WorkflowStartIn):
    return get_enh().workflow.start(workflow_id, body.run_id, body.context)


@router.post("/workflows/runs/{run_id}/advance")
def workflow_advance(run_id: str):
    return get_enh().workflow.advance(run_id)


@router.post("/workflows/runs/{run_id}/approve")
def workflow_approve(run_id: str, body: ApprovalIn):
    return get_enh().workflow.approve(run_id, body.approver)


@router.get("/workflows/runs")
def workflow_runs(limit: int = Query(50, ge=1, le=200)):
    return {"runs": get_enh().workflow.runs(limit)}


@router.get("/workflows/runs/{run_id}")
def workflow_run_detail(run_id: str):
    return get_enh().workflow.run_detail(run_id)


# -------------------------------------------------- 授权引擎骨架（P-09）
class AuthzCheckIn(BaseModel):
    engine: str = "policy"
    subject: dict = {}
    action: str = "read"
    resource: dict = {}


@router.get("/authz/engines")
def authz_engines():
    return {"engines": get_enh().authz.catalog()}


@router.post("/authz/check")
def authz_check(body: AuthzCheckIn):
    try:
        return get_enh().authz.check(body.engine, body.subject, body.action, body.resource)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/authz/audit")
def authz_audit(limit: int = Query(100, ge=1, le=500)):
    return {"checks": get_enh().authz.audit(limit)}


# -------------------------------------------------- 批量动作 / 副作用 / 仿真（P-07 / P-12）
class BatchIn(BaseModel):
    actions: list[dict]
    batch_key: str | None = None


class EditsIn(BaseModel):
    edits: list[dict]
    actor: str = "system"


class SimulationIn(BaseModel):
    actions: list[dict]
    watch: list[str] | None = None
    query: dict | None = None


@router.post("/actions/batch")
def actions_batch(body: BatchIn):
    return get_enh().batch.apply(body.actions, body.batch_key)


@router.post("/actions/batch/plan")
def actions_batch_plan(body: BatchIn):
    return get_enh().batch.plan(body.actions)


@router.post("/edits")
def multi_edits(body: EditsIn):
    return get_enh().batch.edits(body.edits, body.actor)


@router.get("/actions/batch/history")
def batch_history():
    return {"history": get_enh().batch.history()}


@router.get("/effects")
def effect_declarations(action_id: str | None = Query(None)):
    return {"effects": get_enh().effects.declarations(action_id)}


@router.get("/effects/log")
def effect_log(limit: int = Query(100, ge=1, le=500)):
    return {"log": get_enh().effects.log(limit)}


@router.post("/simulate/whatif")
def simulate_whatif(body: SimulationIn):
    sim = get_enh().simulator
    if body.query is not None:
        return sim.compare_query(body.actions, body.query)
    return sim.run(body.actions, body.watch)


# -------------------------------------------------- Agent 工具观测与评测（P-12）
@router.get("/agent/observability")
def agent_observability():
    return get_enh().system.observability.stats()


@router.get("/agent/invocations")
def agent_invocations(limit: int = Query(50, ge=1, le=500)):
    return {"invocations": get_enh().system.observability.recent(limit)}


@router.get("/agent/evalset")
def agent_evalset():
    from freight.observability import list_evalset
    return {"samples": list_evalset()}


@router.post("/agent/eval")
def agent_eval():
    from freight.observability import run_eval
    e = get_enh()
    return run_eval(lambda tool, args: e.system.mcp_call(tool, args))


@router.get("/osdk")
def osdk(target: str = Query("typescript")):
    """OSDK-lite 在线生成：本体 JSON -> TS / Python / OpenAPI 3.1。

    本体演进后重新请求即可同步客户端类型（演示页面据此断言 schema 漂移）。
    """
    import tools.gen_osdk as gen
    e = get_enh()
    sem, ext = e.system.sem, e.ext
    openapi = gen.generate_openapi(sem, ext)
    ts = gen.generate_typescript(sem, ext)
    py = gen.generate_python(sem, ext)
    java = gen.generate_java(sem, ext)
    gql = gen.generate_graphql(sem, ext)
    files = {"typescript": ts, "python": py,
             "openapi": json.dumps(openapi, ensure_ascii=False, indent=1),
             "java": java, "graphql": gql}
    if target not in files:
        raise HTTPException(400, f"OSDK_TARGET_UNKNOWN: {target}")
    derived = sem.raw.get("derived_types", [])
    return {
        "target": target,
        "code": files[target],
        "files": files,
        "stats": {
            "classes": len(sem.classes),
            "derived_types": len(derived),
            "interfaces": len(ext.interfaces),
            "functions": len(ext.function_registry),
            "typescript_lines": ts.count("\n") + 1,
            "python_lines": py.count("\n") + 1,
            "java_lines": java.count("\n") + 1,
            "graphql_lines": gql.count("\n") + 1,
            "openapi_schemas": len(openapi["components"]["schemas"]),
            "derived_in_openapi": [
                d["id"] for d in derived
                if openapi["components"]["schemas"].get(d["id"], {}).get("x-derived")
            ],
        },
    }