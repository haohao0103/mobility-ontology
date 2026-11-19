"""画像语义层交互式诊断 Agent：每一步只做一个真实动作。

复用 freight.agent.LLMClient 的 STG 网关配置，但不复用货运工具面：
画像 Agent 只读，工具全部落在 ProfileService 的语义层接口上。
一次 next() 要么调用一次大模型做决策，要么执行一次语义层工具，
不预置链路、不预跑结果；事实只来自工具返回。
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from freight.agent import LLMClient, LLMError

from .service import ProfileService, now_iso

MAX_TOOL_CALLS = 8
OBSERVATION_LIMIT = 2600
TOOL_OBSERVATION_LIMIT = 3200

TOOL_SPECS = {
    "search_assets": {
        "description": "按自然语言、资产类型或状态检索画像资产，返回资产 ID 与摘要。",
        "args": {"query": "string", "kind": "crowd|tag|strategy|canvas|metric|evidence|version|operator|driver|vehicle|city|order",
                 "status": "string", "limit": "int(1-50)"},
    },
    "get_context": {
        "description": "读取单个资产的分层上下文：OVERVIEW 档案摘要，NETWORK 邻接关系，FULL 含属性/证据/版本/血缘。",
        "args": {"asset_id": "string", "level": "OVERVIEW|NETWORK|FULL"},
    },
    "diagnose_crowd": {
        "description": "人群智能纠错：用结构化业务意图对比圈选规则、标签语义和城市覆盖，返回风险、检查项、缺失城市与修复建议。",
        "args": {"asset_id": "string"},
    },
    "impact_analysis": {
        "description": "本体因果影响面：计算变更会影响的人群、策略、画布、订单、人数与金额；可传 proposed_rule 对比修改前后。",
        "args": {"asset_id": "string", "depth": "int(1-4)",
                 "proposed_rule": "string(optional)"},
    },
    "lineage": {
        "description": "沿 crowd_derived_from 追溯上游来源与下游派生人群。",
        "args": {"asset_id": "string", "depth": "int(1-6)"},
    },
    "trace_evidence": {
        "description": "回读资产的证据、版本与时间线，用于解释结论来源。",
        "args": {"asset_id": "string"},
    },
}

SYSTEM_PROMPT = """你是货拉拉画像语义层的只读诊断 Agent。你的任务是基于真实工具返回，回答业务关于画像人群的问题。

硬性规则：
1. 只能使用给定工具获取事实，禁止臆造资产 ID、规则、城市、指标或影响对象。
2. 判定“人群名称/描述/结构化意图”与“圈选规则”是否一致，必须调用 diagnose_crowd。
3. 影响面分析是核心 Case：必须调用 impact_analysis；需要回答影响对象数、影响人数、已发生订单金额、哪些对象已失效、哪些邻域关系被排除。若 diagnose_crowd 已给出 suggested_rule，应把 proposed_rule 传给 impact_analysis 做修改前后对比。
4. 需要看规则表达式、引用标签、覆盖城市、结构化意图字段，调用 get_context，level=FULL。
5. 只读：不得修改任何资产，也不得调用写接口。
6. 不要用同一参数重复调用同一工具。
7. 最多执行 {max_calls} 次工具调用；已拿到 diagnose_crowd 与 impact_analysis 后应尽快给结论。

工具契约：
{tools}

每次回复只输出一个 JSON 对象，不要 markdown 代码块，不要多余文字：
- 需要继续查证时：
  {{"thought":"为什么调用这个工具","tool":"工具名","args":{{...}}}}
- 信息已足够时：
  {{"thought":"一句话判断依据","final":"给业务人员的结论，必须引用真实数字：风险等级、冲突项、缺失城市数、影响对象数、影响人数、已发生订单金额、被排除的邻域关系，并给出修复方向与是否需要人工确认"}}
""".strip()

JSON_RE = re.compile(r"\{[\s\S]*\}")


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    match = JSON_RE.search(text)
    return match.group(0) if match else text


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


class ProfileReasoningAgent:
    """一个诊断会话；每个 next() 只执行一个真实动作。"""

    def __init__(self, service: ProfileService, question: str,
                 asset_id: str = "", max_calls: int = MAX_TOOL_CALLS):
        self.service = service
        self.question = (question or "").strip() or (
            "这个人群的圈选规则和业务意图一致吗？会影响哪些策略和订单？应该怎么修？"
        )
        self.asset_id = (asset_id or "").strip()
        self.max_calls = max(2, min(int(max_calls), MAX_TOOL_CALLS))
        self.client: Optional[LLMClient] = None
        self.reset()

    # ------------------------------------------------------------------ 状态
    def reset(self) -> None:
        self.steps: List[dict] = []
        self.messages: List[dict] = []
        self.tool_calls = 0
        self.finished = False
        self.pending = "plan"
        self.planned: Optional[dict] = None
        self.final: Optional[str] = None
        self.error: Optional[str] = None
        self.started = False

    def _llm(self) -> LLMClient:
        if self.client is None:
            self.client = LLMClient()
        return self.client

    def start(self) -> dict:
        """只创建会话，不调用大模型，也不查询任何资产。"""
        self.started = True
        tools = "\n".join(
            f"- {name}: {spec['description']} args={json.dumps(spec['args'], ensure_ascii=False)}"
            for name, spec in TOOL_SPECS.items()
        )
        system = SYSTEM_PROMPT.format(max_calls=self.max_calls, tools=tools)
        user = f"业务问题：{self.question}"
        if self.asset_id:
            user += f"\n起点资产：{self.asset_id}"
        self.messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.result()

    def next(self) -> dict:
        """点一次下一步：一次大模型决策，或一次语义层工具执行。"""
        if self.finished:
            return self.result()
        if not self.started:
            return self.start()
        try:
            if self.pending == "plan":
                self._step_plan()
            elif self.pending == "tool":
                self._step_tool()
            else:
                raise ValueError(f"未知的待执行动作：{self.pending}")
        except Exception as exc:
            self.error = str(exc)
            raise
        self.error = None
        return self.result()

    # ------------------------------------------------------------------ 动作
    def _step_plan(self) -> None:
        force_final = self.tool_calls >= self.max_calls
        if force_final:
            self.messages.append({
                "role": "user",
                "content": "工具调用次数已达上限，请基于已有观察直接输出 final 结论 JSON。",
            })
        raw = self._llm().chat(self.messages, thinking=False)
        parsed = self._parse(raw)
        thought = str(parsed.get("thought") or "").strip()
        final = parsed.get("final")
        if isinstance(final, str) and final.strip():
            self.final = final.strip()
            self.finished = True
            self.pending = "finish"
            self._record(
                phase="final", title="形成结论", thought=thought,
                observation=self.final, status="ok",
            )
            self.messages.append({"role": "assistant", "content": raw})
            return
        tool = str(parsed.get("tool") or "").strip()
        if tool not in TOOL_SPECS:
            raise LLMError(f"大模型选择了未知工具：{tool or '空'}")
        args = parsed.get("args") if isinstance(parsed.get("args"), dict) else {}
        self.planned = {"tool": tool, "args": args}
        self.pending = "tool"
        self._record(
            phase="plan", title=f"决定调用 {tool}", thought=thought,
            tool=tool, args=args, status="ok",
            observation="大模型基于当前观察选择下一步工具；尚未执行任何查询。",
        )
        self.messages.append({"role": "assistant", "content": raw})

    def _step_tool(self) -> None:
        assert self.planned, "没有待执行的工具"
        tool = self.planned["tool"]
        args = dict(self.planned.get("args") or {})
        args.pop("_", None)
        api, data = self._call_tool(tool, args)
        self.tool_calls += 1
        observation = _truncate(
            json.dumps(data, ensure_ascii=False, default=str), TOOL_OBSERVATION_LIMIT)
        self._record(
            phase="tool", title=self._tool_title(tool, args),
            tool=tool, args=args, api=api, status="ok",
            observation=observation, data=self._ui_data(tool, data),
        )
        self.messages.append({
            "role": "user",
            "content": f"工具 {tool} 返回（{api}）：{observation}",
        })
        self.planned = None
        self.pending = "plan"
        if self.tool_calls >= self.max_calls:
            self.messages.append({
                "role": "user",
                "content": "工具调用次数已达上限，请直接输出 final 结论 JSON。",
            })

    # ------------------------------------------------------------------ 工具
    def _call_tool(self, tool: str, args: dict) -> tuple[str, Any]:
        if tool == "search_assets":
            query = str(args.get("query") or "")
            kind = args.get("kind") or None
            status = args.get("status") or None
            limit = int(args.get("limit") or 10)
            limit = max(1, min(limit, 50))
            data = self.service.search_assets(
                query=query, kind=kind, status=status, limit=limit)
            api = (f"GET /profile/assets?query={query}&kind={kind or ''}"
                   f"&status={status or ''}&limit={limit}")
            return api, data

        asset_id = str(args.get("asset_id") or self.asset_id)
        if not asset_id:
            raise ValueError("工具缺少 asset_id，且会话没有起点资产")

        if tool == "get_context":
            level = str(args.get("level") or "OVERVIEW").upper()
            data = self.service.get_context(asset_id, level=level)
            return f"GET /profile/assets/{asset_id}/context?level={level}", data

        if tool == "diagnose_crowd":
            data = self.service.diagnose_crowd(asset_id)
            return f"GET /profile/assets/{asset_id}/diagnose", data

        if tool == "impact_analysis":
            depth = max(1, min(int(args.get("depth") or 3), 4))
            proposed_rule = str(args.get("proposed_rule") or "").strip() or None
            data = self.service.impact_analysis(
                asset_id, depth=depth, proposed_rule=proposed_rule)
            url = f"GET /profile/assets/{asset_id}/impact?depth={depth}"
            if proposed_rule:
                url += "&proposed_rule=" + quote(proposed_rule)
            return url, data

        if tool == "lineage":
            depth = max(1, min(int(args.get("depth") or 3), 6))
            data = self.service.lineage(asset_id, depth=depth)
            return f"GET /profile/assets/{asset_id}/lineage?depth={depth}", data

        if tool == "trace_evidence":
            data = self.service.trace_evidence(asset_id)
            return f"GET /profile/assets/{asset_id}/evidence", data

        raise ValueError(f"未知工具：{tool}")

    @staticmethod
    def _tool_title(tool: str, args: dict) -> str:
        labels = {
            "search_assets": "语义检索资产",
            "get_context": "读取分层上下文",
            "diagnose_crowd": "执行人群纠错",
            "impact_analysis": "计算下游影响面",
            "lineage": "追溯派生血缘",
            "trace_evidence": "回读证据与版本",
        }
        return labels.get(tool, tool)

    @staticmethod
    def _ui_data(tool: str, data: Any) -> Any:
        """给前端留一份紧凑结构化结果，避免页面解析大段 JSON。"""
        if not isinstance(data, dict):
            return data
        if tool == "search_assets":
            return {
                "total": data.get("total"),
                "items": [{
                    "id": item.get("id"), "name": item.get("name"),
                    "kind": item.get("kind"), "status": item.get("status"),
                } for item in (data.get("items") or [])[:8]],
            }
        if tool == "get_context":
            props = data.get("properties") or {}
            keep = ("name", "description", "rule_expression", "referenced_tags",
                    "scope", "expected_city_count", "intent_vehicle_type",
                    "intent_sticker_status", "intent_expected_city_count",
                    "intent_managed_cities", "estimated_size", "status", "owner")
            compact = {k: props.get(k) for k in keep if k in props}
            if isinstance(compact.get("scope"), list):
                compact["scope_count"] = len(compact["scope"])
            compact.pop("scope", None)
            return {
                "level": data.get("trust", {}).get("projection"),
                "name": data.get("name"),
                "status": data.get("status"),
                "summary": data.get("summary"),
                "properties": compact,
            }
        if tool == "diagnose_crowd":
            fix = data.get("fix") or {}
            suggestions = data.get("suggestions") or []
            return {
                "risk_level": data.get("risk_level"),
                "verdict": data.get("verdict"),
                "kpi": data.get("kpi"),
                "issues": [{
                    "code": i.get("code"), "severity": i.get("severity"),
                    "message": i.get("message"),
                    "missing_cities": i.get("missing_cities"),
                } for i in (data.get("issues") or [])],
                "suggested_rule": fix.get("suggested_rule"),
                "suggested_action": suggestions[0] if suggestions else None,
            }
        if tool == "impact_analysis":
            summary = data.get("summary") or {}
            nodes = [{
                "id": item.get("id"), "name": item.get("name"),
                "kind": item.get("kind"), "distance": item.get("distance"),
                "via_relation": item.get("via_relation"),
                "impact_type": item.get("impact_type"),
                "active": (item.get("effect") or {}).get("active"),
            } for item in (data.get("affected") or [])]
            return {
                "risk": data.get("risk"),
                "affected_total": summary.get("affected_total"),
                "by_kind": summary.get("by_kind"),
                "by_impact_type": summary.get("by_impact_type"),
                "quantification": data.get("quantification"),
                "verdict": data.get("verdict"),
                "change": data.get("change"),
                "excluded": summary.get("skipped_relation_details"),
                "nodes": nodes,
            }
        if tool == "lineage":
            downstream = (data.get("downstream") or {}).get("nodes") or []
            upstream = (data.get("upstream") or {}).get("nodes") or []
            return {
                "downstream": [{"id": n.get("id"), "name": n.get("name")}
                               for n in downstream],
                "upstream": [{"id": n.get("id"), "name": n.get("name")}
                             for n in upstream],
            }
        if tool == "trace_evidence":
            return {
                "evidence_count": data.get("evidence_count"),
                "version_count": data.get("version_count"),
                "coverage": data.get("coverage"),
                "timeline_count": len(data.get("timeline") or []),
            }
        return data

    @staticmethod
    def _parse(raw: str) -> dict:
        text = _strip_fence(raw)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"大模型输出不是合法 JSON：{_truncate(raw, 300)}") from exc
        if not isinstance(parsed, dict):
            raise LLMError(f"大模型输出不是 JSON 对象：{_truncate(raw, 300)}")
        return parsed

    # ------------------------------------------------------------------ 输出
    def _record(self, phase: str, title: str, thought: str = "",
                observation: str = "", status: str = "ok",
                tool: str = "", args: Optional[dict] = None,
                api: str = "", data: Any = None) -> None:
        self.steps.append({
            "n": len(self.steps) + 1,
            "phase": phase,
            "title": title,
            "thought": thought,
            "tool": tool or None,
            "args": args or None,
            "api": api or None,
            "status": status,
            "observation": observation,
            "data": data,
            "at": now_iso(),
        })

    def result(self) -> dict:
        return {
            "question": self.question,
            "asset_id": self.asset_id or None,
            "steps": self.steps,
            "pending": self.pending,
            "finished": self.finished,
            "final": self.final,
            "tool_calls": self.tool_calls,
            "max_calls": self.max_calls,
            "error": self.error,
        }


# 会话只保存在进程内，TTL 与货运诊断链保持一致。
_SESSIONS: Dict[str, tuple] = {}
SESSION_TTL = 3600


def put_session(session_id: str, agent: ProfileReasoningAgent) -> None:
    _SESSIONS[session_id] = (time.time(), agent)
    now = time.time()
    for key, (ts, _) in list(_SESSIONS.items()):
        if now - ts >= SESSION_TTL:
            _SESSIONS.pop(key, None)


def get_session(session_id: str) -> ProfileReasoningAgent:
    item = _SESSIONS.get(session_id)
    if not item or time.time() - item[0] >= SESSION_TTL:
        _SESSIONS.pop(session_id, None)
        raise ValueError("诊断会话不存在或已过期，请重新开始")
    return item[1]