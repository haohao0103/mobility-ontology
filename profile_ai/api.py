"""画像语义层 FastAPI 路由，统一挂载在 /profile 前缀下。"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from freight.agent import LLMError

from .agent import ProfileReasoningAgent, get_session, put_session
from .hugegraph import HugeGraphError
from .seed import seed_profile
from .service import AssetNotFound, ProfileError, ProfileService

router = APIRouter(prefix="/profile", tags=["profile"])

_UI_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "profile.html"
)
_ORDER_ONTOLOGY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "order-ontology.html"
)
_PROFILE_ONTOLOGY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "profile-ontology.html"
)

_service: Optional[ProfileService] = None


@router.get("/ui", include_in_schema=False)
def profile_ui():
    """画像语义层可视化页面，与 REST API 同源，避免跨域。"""
    if not os.path.exists(_UI_PATH):
        raise HTTPException(status_code=404, detail="可视化页面未部署")
    return FileResponse(
        _UI_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/order-ontology", include_in_schema=False)
def order_ontology_page():
    """货拉拉订单本体演示页，用一单货解释对象、关系、规矩和动作。"""
    if not os.path.exists(_ORDER_ONTOLOGY_PATH):
        raise HTTPException(status_code=404, detail="订单本体页面未部署")
    return FileResponse(
        _ORDER_ONTOLOGY_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@router.get("/ontology", include_in_schema=False)
def profile_ontology_page():
    """画像本体演示页，用一个人群按同一套对象/关系/规矩/动作讲清楚。"""
    if not os.path.exists(_PROFILE_ONTOLOGY_PATH):
        raise HTTPException(status_code=404, detail="画像本体页面未部署")
    return FileResponse(
        _PROFILE_ONTOLOGY_PATH,
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


def get_service() -> ProfileService:
    """进程内单例，构造时不访问网络。"""
    global _service
    if _service is None:
        _service = ProfileService()
    return _service


def _guard(fn):
    try:
        return fn()
    except AssetNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProfileError as e:
        raise HTTPException(status_code=getattr(e, "status_code", 400), detail=str(e))
    except HugeGraphError as e:
        raise HTTPException(status_code=502, detail=f"HugeGraph 调用失败: {e}")
    except LLMError as e:
        raise HTTPException(status_code=502, detail=f"大模型调用失败: {e}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


class ProposeIn(BaseModel):
    action: str
    asset_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    reason: str
    actor: str


class ConfirmIn(BaseModel):
    approver: str
    approve: bool = True


class ChainStartIn(BaseModel):
    question: str = ""
    asset_id: str = ""
    max_calls: int = 8


@router.post("/reasoning-chain/start")
def profile_chain_start(body: ChainStartIn):
    """只创建诊断会话，不调用大模型、不查询事实。"""
    import uuid

    agent = ProfileReasoningAgent(
        get_service(), body.question, asset_id=body.asset_id,
        max_calls=body.max_calls)
    result = _guard(agent.start)
    session_id = uuid.uuid4().hex
    put_session(session_id, agent)
    result["session_id"] = session_id
    return result


@router.post("/reasoning-chain/{session_id}/next")
def profile_chain_next(session_id: str):
    """每次只执行一个动作：一次大模型决策或一次语义层工具调用。"""
    agent = _guard(lambda: get_session(session_id))
    result = _guard(agent.next)
    result["session_id"] = session_id
    return result


@router.get("")
def profile_meta():
    svc = get_service()
    return {
        "name": "画像 AI 语义层",
        "version": svc.semantic.meta.get("version"),
        "graphspace": svc.client.graphspace,
        "graph": svc.client.graph,
        "tools": ["search_assets", "get_context", "validate_crowd",
                  "diagnose_crowd", "scan_crowds", "impact_analysis",
                  "impact_trace", "lineage", "trace_evidence", "query_metric"],
        "write_gate": ["propose_change", "confirm_change"],
        "docs": "/docs",
    }


@router.get("/health")
def profile_health():
    return _guard(lambda: get_service().health())


@router.post("/provision")
def profile_provision():
    """幂等创建 graphspace / graph / schema。"""
    return _guard(lambda: get_service().provision())


@router.get("/dictionary")
def profile_dictionary():
    return _guard(lambda: get_service().dictionary())


@router.post("/seed")
def profile_seed():
    """写入演示资产（8 类顶点 + 10 类时态边），失败时返回违规明细。"""
    return _guard(lambda: seed_profile(get_service()))


@router.get("/assets")
def profile_search(query: str = "", kind: Optional[str] = None,
                   status: Optional[str] = None, limit: int = 20):
    return _guard(lambda: get_service().search_assets(
        query=query, kind=kind, status=status, limit=limit))


@router.get("/assets/{asset_id}/context")
def profile_context(asset_id: str, level: str = "OVERVIEW",
                    at: Optional[str] = None):
    return _guard(lambda: get_service().get_context(
        asset_id, level=level, at=at))


@router.get("/assets/{asset_id}/validate")
def profile_validate(asset_id: str):
    return _guard(lambda: get_service().validate_crowd(asset_id))


@router.get("/corrections")
def profile_corrections(limit: int = 200):
    """P0 人群智能纠错：批量扫描并返回高危清单与 KPI。"""
    return _guard(lambda: get_service().scan_crowds(limit=limit))


@router.get("/assets/{asset_id}/diagnose")
def profile_diagnose(asset_id: str, include_impact: bool = True,
                     at: Optional[str] = None):
    """P0 人群智能纠错：单人群诊断 + 影响面 + 修复建议。"""
    return _guard(lambda: get_service().diagnose_crowd(
        asset_id, include_impact=include_impact, at=at))


@router.get("/assets/{asset_id}/impact")
def profile_impact(asset_id: str, depth: int = 3,
                   at: Optional[str] = None,
                   proposed_rule: Optional[str] = None):
    return _guard(lambda: get_service().impact_analysis(
        asset_id, depth=depth, at=at, proposed_rule=proposed_rule))


@router.get("/assets/{asset_id}/impact-trace")
def profile_impact_trace(asset_id: str, depth: int = 3,
                         at: Optional[str] = None,
                         proposed_rule: Optional[str] = None,
                         step: int = 1):
    """确定性逐步推导：每次只执行一个影响面阶段，返回该步输入与输出。"""
    return _guard(lambda: get_service().impact_trace(
        asset_id, depth=depth, at=at, proposed_rule=proposed_rule, step=step))


@router.get("/assets/{asset_id}/lineage")
def profile_lineage(asset_id: str, depth: int = 3,
                    at: Optional[str] = None):
    """沿 crowd_derived_from 追溯派生血缘，输出上游来源与下游派生路径。"""
    return _guard(lambda: get_service().lineage(asset_id, depth=depth, at=at))


@router.get("/assets/{asset_id}/evidence")
def profile_evidence(asset_id: str, at: Optional[str] = None):
    return _guard(lambda: get_service().trace_evidence(asset_id, at=at))


@router.get("/metrics")
def profile_metrics(asset_id: Optional[str] = None,
                    metric_id: Optional[str] = None,
                    name: Optional[str] = None, limit: int = 20):
    return _guard(lambda: get_service().query_metric(
        asset_id=asset_id, metric_id=metric_id, name=name, limit=limit))


@router.post("/proposals")
def profile_propose(body: ProposeIn):
    return _guard(lambda: get_service().propose_change(
        action=body.action, asset_id=body.asset_id, payload=body.payload,
        reason=body.reason, actor=body.actor))


@router.get("/proposals")
def profile_proposals(status: Optional[str] = None, limit: int = 50):
    return _guard(lambda: get_service().list_proposals(status=status, limit=limit))


@router.post("/proposals/{proposal_id}/confirm")
def profile_confirm(proposal_id: str, body: ConfirmIn):
    return _guard(lambda: get_service().confirm_change(
        proposal_id, approver=body.approver, approve=body.approve))


@router.get("/audit")
def profile_audit_log(limit: int = 100):
    return _guard(lambda: get_service().list_audit(limit=limit))