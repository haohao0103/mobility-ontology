"""真实 Agent 诊断运行时：大模型规划 → MCP 工具调用 → 观察 → 分层记录。

Agent 只读：所有业务事实经 FactMCP 现查现用，不写入本体或事实库。
每一步都显式记录 Agent 提出的问题、TBox 本体依据、ABox 工具返回，供前端分层回放。
"""
from __future__ import annotations

import json
import random
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from .semantic import BusinessReject

DEFAULT_BASE = "http://bdi-gpt-stg.myhll.cn/v1"
DEFAULT_MODEL = "qwen3.6-plus"
OBSERVATION_LIMIT = 2600
# 喂给大模型的 TBox 索引/定义必须设上限，避免本体规模增长后上下文线性膨胀。
CATALOG_PROMPT_LIMIT = 3000
TBOX_CONTEXT_LIMIT = 6000
QUERY_EXPR_LIMIT = 600
# schema search 的种子召回规模；闭包深度保证结构性依赖不被 top-k 截断。
SEARCH_TOP_K = 8
SEARCH_FEE_TOP_K = 4
# LLM 工具调用的默认深度；对象上下文步骤会用逐跳试算决定实际深度。
SCHEMA_DEPTH = 2
SCHEMA_MAX_DEPTH = 6
# 只读诊断 Agent 可见的工具面；动作只允许预检，不允许直接执行。
AGENT_TOOL_PREFIXES = ("ontology.", "function.")
AGENT_EXTRA_TOOLS = {"action.plan"}

FIELD_LABELS = {
    "object_id": "对象 ID", "type": "类型", "type_label": "类型",
    "name": "名称", "status": "状态",
    "fee_id": "费用项", "fee_type": "费用类型", "fee_label": "费用名称",
    "quoted_amount": "报价", "expected_amount": "规则应计",
    "actual_amount": "实收", "waived_amount": "减免",
    "quote_delta": "报价差额", "rule_delta": "规则差额", "calc_basis": "计费依据",
    "evidence_id": "证据 ID", "evidence_type": "证据类型",
    "source_system": "来源系统", "occurred_at": "发生时间",
    "summary": "摘要", "version": "版本",
    "predicate": "谓语", "label": "关系", "subject_id": "主语",
    "subject_name": "主语名称", "object_name": "宾语名称",
    "settlement_id": "结算单", "period": "账期", "amount": "金额",
    "reversed_amount": "冲正金额", "posted_at": "入账时间",
    "invoice_id": "发票", "invoice_no": "发票号", "tax_rate": "税率",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def model_name() -> str:
    return os.environ.get("OPENAI_CHAT_LANGUAGE_MODEL") or DEFAULT_MODEL


def _parse_headers(raw: str) -> dict:
    headers: dict[str, str] = {}
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            key, value = part.split("=", 1)
        elif ":" in part:
            key, value = part.split(":", 1)
        else:
            continue
        headers[key.strip()] = value.strip()
    return headers


def _load_env_file(path: str) -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip().strip('"').strip("'")


class LLMError(RuntimeError):
    pass


class _TransientLLM(LLMError):
    """可重试的网关故障（限流 / 5xx / 网络抖动 / 空响应）。

    区分它与 LLMError 的意义：认证失败、参数错误这类**重试也没用**的错误
    必须立刻抛出，不能靠重试掩盖；只有瞬时故障才值得退避重试。
    """
    pass


class LLMClient:
    """OpenAI 兼容网关客户端，STG 环境变量与 nl2sql 模板保持一致。"""

    def __init__(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _load_env_file(os.path.join(root, ".env"))
        self.base = (os.environ.get("OPENAI_CHAT_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.model = model_name()
        self.key = os.environ.get("OPENAI_CHAT_API_KEY", "")
        self.headers = _parse_headers(os.environ.get("OPENAI_DEFAULT_HEADERS", ""))
        self.timeout = float(os.environ.get("OPENAI_CHAT_TIMEOUT", "120"))
        self.max_tokens = int(os.environ.get("OPENAI_CHAT_STEP_TOKENS", "1600"))
        self.step_thinking = os.environ.get("OPENAI_CHAT_STEP_THINKING", "0") == "1"
        self.final_thinking = os.environ.get("OPENAI_CHAT_FINAL_THINKING", "1") == "1"
        self.thinking_budget = int(os.environ.get("OPENAI_CHAT_THINKING_BUDGET", "256"))
        # 退避重试：实测推理链偶发 503（网关瞬时故障），重跑即恢复。
        # 没有重试时，一次抖动就会让整个诊断链 503 失败。
        self.retry_attempts = int(os.environ.get("OPENAI_CHAT_RETRY_ATTEMPTS", "3"))
        self.retry_base = float(os.environ.get("OPENAI_CHAT_RETRY_BASE", "1.5"))
        self.retry_max = float(os.environ.get("OPENAI_CHAT_RETRY_MAX", "8"))
        if not self.key:
            raise LLMError("未配置 OPENAI_CHAT_API_KEY，无法启动 Agent")

    # 可重试的 HTTP 状态码：限流与网关侧瞬时故障
    RETRYABLE_HTTP = {429, 500, 502, 503, 504}

    def chat(self, messages: list[dict], thinking: bool | None = None,
             attempts: int | None = None) -> str:
        """调用网关；瞬时故障按指数退避重试。

        重试只针对「重试有意义」的故障：429 / 5xx / 网络抖动 / 空响应。
        认证失败（401/403）与参数错误（400）立即抛出，不浪费额度。
        """
        thinking = self.step_thinking if thinking is None else thinking
        budget = attempts if attempts is not None else self.retry_attempts
        if thinking and attempts is None:
            # 长推理单次耗时高，总时长敏感：默认少给一次重试
            budget = max(1, self.retry_attempts - 1)
        last: Exception | None = None
        for attempt in range(1, max(1, budget) + 1):
            try:
                return self._chat_once(messages, thinking)
            except _TransientLLM as exc:
                last = exc
                if attempt >= budget:
                    break
                delay = min(self.retry_base * (2 ** (attempt - 1)), self.retry_max)
                # 加抖动：多个并发请求别踩在同一秒重试
                delay *= 0.8 + random.random() * 0.4
                time.sleep(delay)
        raise LLMError(str(last) if last else "大模型网关不可用")

    def _chat_once(self, messages: list[dict], thinking: bool) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": self.max_tokens,
            "enable_thinking": thinking,
            "stream": True,
        }
        if thinking:
            # 网关不支持非流式长推理；流式返回可持续续期，避免 nginx 60s 超时。
            payload["thinking_budget"] = self.thinking_budget
        request = urllib.request.Request(
            f"{self.base}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self.key}",
                **self.headers,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                content, reasoning = self._read_response(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            message = f"大模型网关 HTTP {exc.code}: {detail}"
            if exc.code in self.RETRYABLE_HTTP:
                raise _TransientLLM(message) from exc
            raise LLMError(message) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise _TransientLLM(f"大模型网关不可用: {exc}") from exc
        except Exception as exc:
            raise LLMError(f"大模型网关不可用: {exc}") from exc
        content = (content or "").strip()
        if not content:
            content = (reasoning or "").strip()
        if not content:
            raise _TransientLLM("大模型返回为空")
        return content

    @staticmethod
    def _read_response(resp) -> tuple[str, str]:
        """读取 SSE 流；网关忽略 stream 时回退解析普通 JSON。"""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        raw_lines: list[str] = []
        saw_sse = False
        for raw in resp:
            text = raw.decode("utf-8", "replace")
            line = text.strip()
            if not line:
                continue
            if not line.startswith("data:"):
                raw_lines.append(text)
                continue
            saw_sse = True
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("error"):
                raise LLMError(
                    f"大模型网关返回错误: {json.dumps(obj['error'], ensure_ascii=False)[:300]}"
                )
            for choice in obj.get("choices") or []:
                delta = choice.get("delta") or choice.get("message") or {}
                content = delta.get("content")
                reasoning = delta.get("reasoning_content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
        if not saw_sse:
            body = json.loads("".join(raw_lines)) if raw_lines else {}
            choices = body.get("choices") or []
            if not choices:
                raise LLMError(
                    f"大模型返回为空: {json.dumps(body, ensure_ascii=False)[:300]}"
                )
            message = choices[0].get("message") or {}
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            return str(content), str(reasoning)
        return "".join(content_parts), "".join(reasoning_parts)


class ReasoningAgent:
    def __init__(self, system, waybill_id: str, max_steps: int = 6,
                 question: str = ""):
        self.system = system
        self.sem = system.sem
        self.facts = system.facts
        if waybill_id.startswith("waybill:"):
            self.object_id = waybill_id
            self.waybill_id = waybill_id.split(":", 1)[1]
        else:
            self.object_id = f"waybill:{waybill_id}"
            self.waybill_id = waybill_id
        raw_type = self.object_id.split(":", 1)[0]
        self.object_type = next(
            (tid for tid in self.sem.classes if tid.lower() == raw_type.lower()),
            raw_type,
        )
        self.max_steps = max(2, min(int(max_steps), 10))
        self.question_is_default = not (question or "").strip()
        self.question = (
            (question or "").strip()
            or f"诊断运单 {self.waybill_id} 的费用构成与差异原因"
        )
        # 知识型/实例型分诊（确定性，0 LLM）：知识型问题的答案在 TBox 声明
        # 里，不该被拖进 ABox 取证循环。分诊只用本体声明与问句本身，
        # 失败时保守按 instance 走原诊断链路。
        try:
            from freight.answerability import classify_question
            self.question_kind = classify_question(
                self.question, semantic=self.sem,
            )["kind"]
        except Exception:  # noqa: BLE001
            self.question_kind = "instance"
        # LLM 客户端延迟到第一次真正调用才构造。start()/catalog/tbox 这些
        # 确定性步骤不该被网关或密钥问题挡住；演示前另走 /agent/preflight 预检。
        self._client: "LLMClient | None" = None
        # 对象级能力集：只暴露当前对象可用的通用原语、确定性函数和动作预检。
        self.catalog = {
            t["name"]: t for t in self.facts.tools_for(self.object_type)["tools"]
            if t["name"].startswith(AGENT_TOOL_PREFIXES)
            or t["name"] in AGENT_EXTRA_TOOLS
        }
        self.tbox_seen: dict[str, dict] = {}
        self.types_seen: set[str] = set()
        self.fee_types_seen: set[str] = set()
        self.catalog_index: list[dict] = []
        self.catalog_mode = "full"
        self.catalog_fits = False
        self.catalog_group_ids: list[str] = []
        self.catalog_aggregate_ids: list[str] = []
        self.catalog_anchor_ids: list[str] = []
        self.catalog_context = ""

    @property
    def client(self) -> "LLMClient":
        if self._client is None:
            self._client = LLMClient()
        return self._client

    @property
    def model_label(self) -> str:
        """result() 用的模型名：绝不触发 LLM 客户端构造。"""
        return self._client.model if self._client is not None else model_name()

    @property
    def endpoint_label(self) -> str:
        base = (self._client.base if self._client is not None
                else (os.environ.get("OPENAI_CHAT_API_BASE") or DEFAULT_BASE).rstrip("/"))
        return f"{base}/chat/completions"

    # ------------------------------------------------------------------ 主循环
    def _reset(self) -> None:
        self.started_at = time.perf_counter()
        self.steps: list[dict] = []
        self.observations: list[dict] = []
        self.messages: list[dict] = []
        self.catalog_index = []
        self.catalog_mode = "full"
        self.catalog_fits = False
        self.catalog_group_ids = []
        self.catalog_anchor_ids = []
        self.catalog_context = ""
        self.tbox_queries = 0
        self.final_action: dict | None = None
        self.error: str | None = None
        self.degraded = False
        self.finished = False
        self.reviewed = False
        self.started = False
        self.pending = "catalog"
        self.planned: dict = {}
        self.pending_action: dict | None = None
        self.plan: dict | None = None

    def start(self) -> dict:
        """只创建会话并等待用户点击，不调用大模型，也不查询任何业务事实。"""
        self._reset()
        self.started = True
        return self.result()

    def next(self) -> dict:
        """用户点一次下一步，只执行一个动作：LLM、本体查询或事实查询。"""
        if self.finished:
            return self.result()
        if not self.started:
            return self.start()
        try:
            if self.pending == "catalog":
                self._step_catalog()
            elif self.pending == "plan":
                self._step_plan()
            elif self.pending == "tbox":
                self._step_tbox()
            elif self.pending == "tbox_query":
                self._step_tbox_query()
            elif self.pending == "tbox_search":
                self._step_tbox_search()
            elif self.pending == "object_type_search":
                self._step_object_type_search()
            elif self.pending == "tbox_path":
                self._step_tbox_path()
            elif self.pending == "decide":
                self._step_decide()
            elif self.pending == "tool":
                self._step_tool()
            elif self.pending == "review":
                self._step_review()
            elif self.pending == "action":
                self._step_action()
            elif self.pending == "finish":
                self._step_finish()
            else:
                raise ValueError(f"未知的待执行动作：{self.pending}")
        except LLMError as exc:
            # 大模型这一步彻底失败（重试用尽 / 连续空响应）：**不抛给调用方**。
            # 演示与线上排查都需要一个"基于已有观察的结论"，而不是整条链 503。
            # 这里降级成初步结论并结束——本体取到的事实仍然有效。
            self.error = str(exc)
            self.degraded = True
            self.finished = True
            self.pending = None
            self.steps.append(self._degraded_step(exc))
            return self.result()
        except Exception as exc:
            # 单步失败必须显式抛给调用方，不能把失败伪装成上一笔已执行步骤。
            self.error = str(exc)
            raise
        return self.result()

    def _closing_datasets(self) -> dict:
        """收口三件套：假设核对 / 计划执行对照 / 角色出口 / 未决项。

        全部确定性产出（finding 码、计数、action.plan），模型只能叙述不能改写。
        正常结论与降级结论共用——链没跑完时"假设全部待补证、任务全部未执行"
        本身就是诚实的收口，不能因为大模型挂了就让收口消失。
        """
        # 规划 LLM 失败时 planned 可能还没建出来：兜底到标准假设/任务，
        # 让假设核对与执行对照表仍然有内容可对照。
        if not self.planned.get("hypotheses"):
            self.planned["hypotheses"] = self._normalize_hypotheses(None)
        if not self.planned.get("tasks"):
            focus = self.planned.get("focus_fee_types") or [""]
            self.planned["tasks"] = self._normalize_tasks(None, focus)
        datasets: list[dict] = []
        facts: list[dict] = []
        checks: list[dict] = []
        # ① 假设核对：每条假设一个裁定（成立/排除/待补证）+ 证据
        hypo_rows = self._hypothesis_verdicts()
        if hypo_rows:
            datasets.append({
                "caption": "假设核对 · 结论必须逐条回应",
                "columns": [
                    {"key": "id", "label": "编号"},
                    {"key": "claim", "label": "假设"},
                    {"key": "verdict", "label": "裁定"},
                    {"key": "evidence", "label": "证据"},
                ],
                "rows": hypo_rows,
                "note": "裁定来自确定性检查（finding 码 / 计数），不是模型措辞",
            })
        # ② 计划执行对照：每条取证任务一个状态，未执行给理由
        review_rows = self._plan_execution_review()
        if review_rows:
            datasets.append({
                "caption": "计划执行对照 · 取证任务清单",
                "columns": [
                    {"key": "intent", "label": "任务"},
                    {"key": "tool", "label": "工具"},
                    {"key": "status", "label": "状态"},
                    {"key": "evidence", "label": "执行证据"},
                    {"key": "reason", "label": "未执行理由"},
                ],
                "rows": review_rows,
                "note": "规划驱动执行：未执行的任务必须给出理由",
            })
        # ③ 角色出口：谁、做什么、对谁做、为什么（来自系统动作提案）
        role_rows = self._role_exports()
        if role_rows:
            datasets.append({
                "caption": "按角色的下一步（动作未执行，等人工确认）",
                "columns": [
                    {"key": "role", "label": "角色"},
                    {"key": "action", "label": "动作"},
                    {"key": "target", "label": "对象"},
                    {"key": "why", "label": "依据"},
                ],
                "rows": role_rows,
                "note": "来自 action.plan 的确定性提案；执行需人工按角色确认",
            })
        open_items = self._open_items()
        if open_items:
            datasets.append({
                "caption": "未决项（缺什么、找谁、哪个系统）",
                "columns": [
                    {"key": "missing", "label": "缺少"},
                    {"key": "owner", "label": "建议找谁"},
                    {"key": "system", "label": "系统"},
                    {"key": "blocks", "label": "阻塞"},
                ],
                "rows": open_items,
                "note": "结论的边界：这些项补齐前，相关口径只能算到这一步",
            })
        verdict_counts: dict[str, int] = {}
        for row in hypo_rows:
            verdict_counts[row["verdict"]] = verdict_counts.get(row["verdict"], 0) + 1
        facts.extend([
            {"label": "假设裁定", "value": "、".join(
                f"{k} {v}" for k, v in sorted(verdict_counts.items())) or "—"},
            {"label": "任务执行", "value": "、".join([
                f"已执行 {sum(1 for r in review_rows if r['status'] == '已执行')}",
                f"未执行 {sum(1 for r in review_rows if r['status'] == '未执行')}",
                f"计划外 {sum(1 for r in review_rows if r['status'] == '计划外执行')}",
            ]) if review_rows else "—"},
            {"label": "角色出口", "value": f"{len(role_rows)} 条"},
            {"label": "未决项", "value": f"{len(open_items)} 条"},
        ])
        checks.append({
            "pass": all(row["verdict"] for row in hypo_rows),
            "label": "每条假设都有裁定",
            "expected": f"{len(hypo_rows)} 条全部裁定",
            "actual": "、".join(f"{k} {v}" for k, v in sorted(verdict_counts.items())) or "无假设",
        })
        checks.append({
            "pass": all(row["reason"] != "—" for row in review_rows
                        if row["status"] == "未执行"),
            "label": "未执行任务已给出理由",
            "expected": "全部有理由",
            "actual": "全部有理由" if all(
                row["reason"] != "—" for row in review_rows
                if row["status"] == "未执行") else "存在无理由的未执行任务",
        })
        return {"datasets": datasets, "facts": facts, "checks": checks}

    def _degraded_step(self, exc: Exception) -> dict:
        """大模型失败时的降级步骤。

        字段必须与 _final_step/_llm_step 对齐（query/facts/checks/data/
        judgment/conclusion/source/basis），否则前端 renderChainStep 会在
        step.query.label 上崩掉——降级不但要给结论，还得渲染得出来。
        """
        degraded = self._degraded_final("")
        closing = self._closing_datasets()
        return {
            "index": len(self.steps) + 1,
            "kind": "llm", "tag": "LLM · 失败降级", "actor": "agent",
            "who": "大模型",
            "action": "大模型调用失败，改用已取得的观察归纳",
            "output": str(exc)[:200], "error": str(exc)[:200],
            "answer": degraded["answer"],
            "title": degraded["title"],
            "question": self.question,
            "thought": degraded["thought"],
            "query": {
                "label": "大模型调用失败 → 降级归纳",
                "expression": "degraded_final()",
                "api": "不调用大模型 · 用已取得的观察作答",
                "params": {},
            },
            "data": {"datasets": closing["datasets"]},
            "facts": [{"label": "失败原因", "value": str(exc)[:120]},
                      {"label": "观察条数", "value": f"{len(self.observations)} 条"}]
                     + closing["facts"],
            "checks": [{"pass": False, "label": "大模型调用成功",
                        "expected": "正常返回", "actual": str(exc)[:80]}]
                       + closing["checks"],
            "judgment": degraded["thought"],
            "conclusion": degraded["answer"],
            "source": "降级路径：已有 ABox 观察，未经本轮大模型综合",
            "basis": "—",
            "elapsed_ms": 0, "ok": False,
        }

    def run(self) -> dict:
        """兼容原接口：一次跑完整条链，内部仍然复用逐个动作执行逻辑。"""
        self.start()
        while not self.finished:
            self.next()
        return self.result()

    # ------------------------------------------------------------------ 逐个动作
    def _schema_closure(self) -> tuple[dict, int, list[dict]]:
        """从当前对象类型沿强依赖白名单逐跳扩展，直到新增类型和关系都为 0。"""
        previous_types: set[str] = set()
        previous_relations: set[str] = set()
        chosen: dict | None = None
        max_depth = self.sem.closure_max_depth(SCHEMA_MAX_DEPTH)
        chosen_depth = max_depth
        trace: list[dict] = []
        for depth in range(1, max_depth + 1):
            found = self.sem.schema(
                None,
                types=[self.object_type],
                top_k=SEARCH_TOP_K,
                depth=depth,
                fee_top_k=SEARCH_FEE_TOP_K,
                closure="strong",
            )
            type_ids = {cls["type"] for cls in found["classes"]}
            relation_ids = {rel["id"] for rel in found["relations"]}
            added_types = type_ids - previous_types
            added_relations = relation_ids - previous_relations
            trace.append({
                "depth": depth,
                "types": len(type_ids),
                "relations": len(relation_ids),
                "added_types": len(added_types),
                "added_relations": len(added_relations),
            })
            if chosen is not None and not added_types and not added_relations:
                break
            chosen = found
            chosen_depth = depth
            previous_types = type_ids
            previous_relations = relation_ids
        if chosen is None:
            chosen = self.sem.schema(
                None,
                types=[self.object_type],
                top_k=SEARCH_TOP_K,
                depth=max_depth,
                fee_top_k=SEARCH_FEE_TOP_K,
                closure="strong",
            )
        return chosen, chosen_depth, trace

    def _step_catalog_closure(self) -> None:
        """旧版全局强依赖闭包路径，仅保留作大目录降级参考，不再作为默认主路径。"""
        found, depth_used, depth_trace = self._schema_closure()
        types = [cls["type"] for cls in found["classes"]]
        fee_types = list(found["fee_types"])
        items: list[dict] = []
        for cls in found["classes"]:
            props = cls.get("properties") or []
            detail = (
                f'主键 {cls.get("key") or "—"} · 属性 '
                + " / ".join(map(str, props[:6]))
            )
            if len(props) > 6:
                detail += f" 等 {len(props)} 项"
            detail += f' · {cls.get("reason", "邻接闭包")}'
            items.append({
                "kind": "class", "id": cls["type"],
                "label": cls["label"], "detail": detail,
            })
        for rel in found["relations"]:
            detail = (
                f'{rel["domain"]} → {rel["range"]} · '
                + ("闭包边界" if rel.get("boundary") else "两端均在范围内")
            )
            items.append({
                "kind": "relation", "id": rel["id"],
                "label": rel.get("label", rel["id"]), "detail": detail,
            })
        for fee_type in fee_types:
            items.append({
                "kind": "pricing", "id": f"pricing.{fee_type}",
                "label": fee_type, "detail": "对象邻域内的费用类型",
            })
        for action in self.sem.actions.values():
            if action.get("target") != self.object_type:
                continue
            from_status = "/".join(action.get("from_status") or []) or "任意"
            items.append({
                "kind": "action",
                "id": action["id"],
                "label": action.get("label", action["id"]),
                "detail": (
                    f'角色 {",".join(action.get("roles") or []) or "—"} · '
                    f'允许状态 {from_status} → {action.get("to_status") or "—"}'
                ),
            })
        for function in self.sem.functions.values():
            if function.get("target") != self.object_type:
                continue
            items.append({
                "kind": "function",
                "id": f'function.{function["id"]}',
                "label": function.get("label", function["id"]),
                "detail": (
                    f'{function.get("description") or "确定性函数"} · '
                    f'返回 {function.get("returns", "object")}'
                ),
            })
        self.catalog_index = items

        type_set = set(types)
        required_types = self.sem.diagnosis_required_types(self.object_type)
        covered = [t for t in required_types if t in type_set]
        missing = [t for t in required_types if t not in type_set]
        closure_stable = bool(depth_trace) and (
            depth_trace[-1]["added_types"] == 0
            and depth_trace[-1]["added_relations"] == 0
        )
        if closure_stable:
            if depth_used <= 1:
                depth_reason = (
                    f"1 跳得到 {depth_trace[0]['types']} 个类型；"
                    f"第 2 跳新增类型和关系都为 0，闭包在 1 跳已稳定，"
                    f"所以采用 1 跳结果。"
                )
            else:
                depth_reason = (
                    f"1 跳得到 {depth_trace[0]['types']} 个类型；"
                    f"逐跳试算到 {depth_used} 跳得到 {len(types)} 个类型，"
                    f"第 {depth_used + 1} 跳新增类型和关系都为 0，闭包已稳定，"
                    f"所以采用 {depth_used} 跳结果。"
                )
        else:
            max_depth = self.sem.closure_max_depth(SCHEMA_MAX_DEPTH)
            depth_reason = (
                f"已试到最大 {max_depth} 跳仍有新增类型，"
                f"当前按最大深度 {depth_used} 跳处理。"
            )
        trace_text = "；".join(
            f'{item["depth"]} 跳 {item["types"]} 类/{item["relations"]} 关系'
            f'（新增 {item["added_types"]} 类）'
            for item in depth_trace
        )
        closure = found.get("closure") or {}
        whitelist_count = len(closure.get("relation_ids") or [])
        excluded_count = len(closure.get("excluded_relation_ids") or [])
        input_params = {"types": [self.object_type], "depth": "auto"}
        output_scope = {"types": types, "fee_types": fee_types}
        expression = (
            f'ontology.schema(types=[{json.dumps(self.object_type)}], '
            f'depth="auto")'
        )
        resolved = {
            "depth": depth_used,
            "closure_stable": closure_stable,
            "closure_mode": closure.get("mode", "strong"),
            "closure_relations": whitelist_count,
            "excluded_relations": excluded_count,
            "types": len(types),
            "relations": len(found["relations"]),
        }
        step = self._tbox_step(items, output_scope, {}, len(self.steps) + 1)
        step["title"] = "对象类型强依赖闭包 + 能力集（不调用大模型）"
        step["tag"] = "TBox · 对象上下文"
        step["query"]["label"] = "当前对象类型 → 强依赖闭包 + 可用能力"
        step["query"]["expression"] = expression
        step["query"]["api"] = (
            "semantic_layer.json · schema(types=[object_type], depth=auto)"
            " + tools_for(object_type)"
        )
        step["query"]["params"] = input_params
        step["query"]["input"] = input_params
        step["query"]["scope"] = output_scope
        step["query"]["output"] = output_scope
        step["query"]["scope_label"] = "强依赖闭包输出（只沿白名单关系展开，不是查询入参）"
        step["query"]["input_explain"] = (
            f"types 只填 {self.object_type}：它来自对象 ID 的前缀，是已知实体类型；"
            'depth="auto" 表示从 1 跳开始逐跳试算；结构扩展固定在内部沿 '
            f"semantic_layer.json 的 {whitelist_count} 条强依赖关系展开，"
            f"不遍历另外 {excluded_count} 条证据/轨迹/异常弱关联。"
            f"求解结果是 {depth_used} 跳。"
        )
        step["query"]["output_explain"] = (
            f"{len(types)} 个类型和 {len(fee_types)} 个费用类型是语义层沿强依赖白名单展开的输出，"
            "不是提前拿到的运单、费用或结算数据。"
        )
        step["query"]["depth_trace"] = depth_trace
        step["query"]["resolved"] = resolved
        step["query"]["depth_reason"] = (
            depth_reason
            + f" 闭包只沿 {whitelist_count} 条强依赖关系展开，排除 {excluded_count} 条弱关联。"
        )
        step["query"]["coverage"] = {
            "required": required_types,
            "covered": covered,
            "missing": missing,
            "source": "semantic_layer.json · diagnosis.required_relations",
        }
        step["layers"]["tbox"]["source"] = "semantic_layer.json · 对象类型强依赖闭包"
        step["layers"]["tbox"]["query"] = expression
        step["layers"]["tbox"]["query_explain"] = (
            "大白话：先只给种子类型，只沿强依赖关系白名单从 1 跳开始逐跳试算；"
            "当再扩一跳不再出现新类型和新关系时停止。"
            f"本 case 停在 {depth_used} 跳，第 {depth_used + 1} 跳没有新增；"
            f"白名单 {whitelist_count} 条关系，排除 {excluded_count} 条证据/轨迹/异常弱关联。"
        )
        step["layers"]["tbox"]["returned_explain"] = (
            "大白话：返回的是本体定义和能力清单，不是运单金额；"
            "强依赖类型是语义层按白名单展开的输出，不是查询入参。"
        )
        step["layers"]["tbox"]["note"] = (
            f"页面把输入和输出分开：输入只有 {self.object_type}；"
            f"{len(types)} 个强依赖类型由 semantic_layer.json 的闭包白名单展开得到，"
            "不是遍历全部关系。"
        )
        step["layers"]["join"] = (
            "本步是确定性前置，不调用大模型；只读 TBox，不读 ABox。"
            "下一步才由大模型在能力集内做函数编排。"
        )
        step["operations"] = [
            f"当前对象：{self.object_id}（类型 {self.object_type}）",
            f"schema 入参只传种子类型 {self.object_type}，depth 从 1 逐跳试算",
            f"结构扩展使用强依赖白名单：只沿 {whitelist_count} 条关系展开，"
            f"排除 {excluded_count} 条弱关联",
            trace_text,
            f"结构依赖检查：{len(covered)}/{len(required_types)} 覆盖"
            + (f"，缺少 {'、'.join(missing)}" if missing else ""),
            f"按 target={self.object_type} 生成对象级能力集，共 {len(self.catalog)} 个",
            "本步不加载任何业务事实",
        ]
        step["facts"] = [
            {"label": "种子类型", "value": self.object_type},
            {"label": "强依赖类型", "value": str(len(types))},
            {"label": "闭包深度", "value": f"{depth_used} 跳"},
            {"label": "白名单关系", "value": f"{whitelist_count} 条（排除 {excluded_count} 条）"},
            {"label": "费用候选", "value": str(len(fee_types))},
            {"label": "结构依赖", "value": f"{len(covered)}/{len(required_types)}"},
            {"label": "可用能力", "value": str(len(self.catalog))},
            {"label": "业务事实", "value": "0 行（尚未查询）"},
        ]
        step["checks"] = [
            {"pass": bool(types), "label": "强依赖类型非空",
             "expected": "≥1 个类型", "actual": f"{len(types)} 个"},
            {"pass": bool(found["relations"]), "label": "强依赖关系非空",
             "expected": "≥1 条关系", "actual": f"{len(found['relations'])} 条"},
            {"pass": bool(fee_types), "label": "费用候选非空",
             "expected": "≥1 个费用类型", "actual": f"{len(fee_types)} 个"},
            {"pass": not missing, "label": "本 case 结构依赖覆盖",
             "expected": "、".join(required_types),
             "actual": f"{len(covered)}/{len(required_types)}"
                       + (f"，缺 {'、'.join(missing)}" if missing else "，全部覆盖")},
            {"pass": closure_stable, "label": "强依赖闭包已稳定",
             "expected": "再加一跳无新增", "actual": "是" if closure_stable else f"否，已到 {self.sem.closure_max_depth(SCHEMA_MAX_DEPTH)} 跳"},
            {"pass": excluded_count > 0, "label": "弱关联未进入闭包",
             "expected": "至少排除 1 条非依赖关系",
             "actual": f"白名单 {whitelist_count} 条，排除 {excluded_count} 条"},
            {"pass": True, "label": "本步未访问业务系统",
             "expected": "0 行业务事实", "actual": "0 行"},
        ]
        step["judgment"] = (
            f"{depth_reason} "
            f"本体声明的 {len(required_types)} 个结构依赖已覆盖 "
            f"{len(covered)} 个"
            + (f"，缺 {'、'.join(missing)}" if missing else "")
            + f"；对象级能力集 {len(self.catalog)} 个。"
        )
        step["conclusion"] = (
            "这一步只确定后续允许看哪些本体定义和能力，没有读取任何运单金额；"
            f"{len(types)} 个强依赖类型由白名单关系逐跳试算展开，"
            "不是遍历全部关系，也不是大模型猜的。"
        )
        step["next"] = (
            f"下一步由大模型在 {len(self.catalog)} 个对象能力内做函数编排，"
            "决定第一个业务查询。"
        )
        step["source"] = "TBox：semantic_layer.json；ABox：未查询"
        step["basis"] = (
            f'ontology.schema(types=[{self.object_type}], depth={depth_used})'
            f' · 闭包稳定 · 结构依赖 {len(covered)}/{len(required_types)}'
        )
        self.steps.append(step)
        self.pending = "plan"

    def _catalog_items(self, payload: dict,
                       anchor_ids: list[str] | None = None) -> list[dict]:
        """把 ontology.catalog 的紧凑目录转成前端与模型共用的 TBox 条目。"""
        group_labels = {
            group["id"]: group.get("label", group["id"])
            for group in self.sem.object_type_groups
        }
        anchor_set = set(anchor_ids or [])
        items: list[dict] = []
        for cls in payload.get("types") or []:
            groups = [
                group_labels.get(gid, gid)
                for gid in (cls.get("groups") or [])
            ]
            detail = "对象类型组：" + " / ".join(groups) if groups else "全量紧凑目录"
            if cls["id"] in anchor_set:
                detail += " · 检索锚点"
            items.append({
                "kind": "class",
                "id": cls["id"],
                "label": cls.get("label", cls["id"]),
                "detail": detail,
            })
        for rel in payload.get("relations") or []:
            items.append({
                "kind": "relation",
                "id": rel["id"],
                "label": rel.get("label", rel["id"]),
                "detail": f'{rel["domain"]} → {rel["range"]} · 紧凑目录关系',
            })
        for fee_type in payload.get("fee_types") or []:
            items.append({
                "kind": "pricing",
                "id": f"pricing.{fee_type}",
                "label": fee_type,
                "detail": "目录内的费用类型",
            })
        for action in self.sem.actions.values():
            if action.get("target") != self.object_type:
                continue
            from_status = "/".join(action.get("from_status") or []) or "任意"
            items.append({
                "kind": "action",
                "id": action["id"],
                "label": action.get("label", action["id"]),
                "detail": (
                    f'角色 {",".join(action.get("roles") or []) or "—"} · '
                    f'允许状态 {from_status} → {action.get("to_status") or "—"}'
                ),
            })
        for function in self.sem.functions.values():
            if function.get("target") != self.object_type:
                continue
            items.append({
                "kind": "function",
                "id": f'function.{function["id"]}',
                "label": function.get("label", function["id"]),
                "detail": (
                    f'{function.get("description") or "确定性函数"} · '
                    f'返回 {function.get("returns", "object")}'
                ),
            })
        self.catalog_index = items
        self.types_seen.update(
            cls["id"] for cls in payload.get("types") or []
        )
        self.fee_types_seen.update(payload.get("fee_types") or [])
        for item in items:
            self.tbox_seen[item["id"]] = item
        return items

    @staticmethod
    def _catalog_context(payload: dict, mode: str,
                         anchors: list[str], aggregate_ids: list[str]) -> str:
        """把紧凑目录压成可直接注入模型上下文的文本。

        v4 聚合模式：按「上下文 → 聚合 → 类型」渲染层级；跨聚合边界链接
        带 boundary 标记与端点聚合名（v3 在这里把跨组关系整体丢弃，
        导致规划器看不到任何跨域路径）。
        """
        label = "全量" if mode == "full" else "聚合缩域"
        lines = [
            f"【紧凑 TBox 目录（{label}；{payload.get('characters', 0)} 字符）】",
            "只含类型 ID、名称、聚合归属和关系端点，不含对象实例。",
        ]
        if aggregate_ids:
            lines.append("注入聚合：" + "、".join(aggregate_ids))
        if anchors:
            lines.append("检索锚点：" + "、".join(anchors))
        for group in payload.get("groups") or []:
            if group.get("aggregates"):
                # 聚合模式层级：上下文 → 聚合（根 + 成员）
                lines.append(
                    f"context|{group['id']}|{group.get('label', group['id'])}"
                )
                for agg in group["aggregates"]:
                    lines.append(
                        f"aggregate|{agg['id']}|{agg.get('label', agg['id'])}"
                        f"|root={agg.get('root')}"
                        f"|{'/'.join(agg.get('types') or [])}"
                    )
            else:
                members = group.get("types")
                detail = "/".join(members) if members else \
                    f"{group.get('type_count', '?')} 个类型"
                lines.append(
                    f'group|{group["id"]}|{group.get("label", group["id"])}'
                    f'|{detail}'
                )
        for cls in payload.get("types") or []:
            agg = cls.get("aggregate") or ""
            groups = "、".join(cls.get("groups") or [])
            suffix = f"|aggregate={agg}" if agg else f"|groups={groups or '—'}"
            lines.append(
                f'class|{cls["id"]}|{cls.get("label", cls["id"])}'
                f'{suffix}'
            )
        for rel in payload.get("relations") or []:
            boundary = ""
            if rel.get("boundary"):
                boundary = (
                    f"|boundary={rel.get('domain_aggregate', '?')}"
                    f"→{rel.get('range_aggregate', '?')}"
                )
            lines.append(
                f'relation|{rel["id"]}|{rel.get("label", rel["id"])}'
                f'|{rel["domain"]}->{rel["range"]}{boundary}'
            )
        for fee_type in payload.get("fee_types") or []:
            lines.append(f"pricing|{fee_type}")
        text = "\n".join(lines)
        if len(text) > TBOX_CONTEXT_LIMIT:
            text = (
                text[:TBOX_CONTEXT_LIMIT]
                + "\n…（目录已截断；请用 ontology.object_type_search 缩小范围）"
            )
        return text

    def _aggregates_for_anchors(self, anchors: list[str]) -> list[str]:
        """锚点 → 聚合 id，按锚点 RRF 排名保序（不是按聚合声明顺序）。"""
        return self.sem.aggregate_ids_for_types(anchors)

    def _fit_aggregates_to_budget(self, aggregate_ids: list[str],
                                  budget: int) -> tuple[list[str], bool, list[str]]:
        """按顺序累加聚合，实际测字符数；放不下的跳过，不连坐后面的聚合。

        只截断「装不进预算」的聚合，而不是一遇超预算就 break——否则排在
        后面的小聚合会被无辜砍掉，最容易把核心聚合挤出去。
        """
        selected: list[str] = []
        trimmed = False
        skipped: list[str] = []
        for aid in aggregate_ids:
            trial = selected + [aid]
            payload = self.sem.catalog(aggregate_ids=trial)
            if int(payload.get("characters") or 0) <= budget:
                selected = trial
            else:
                trimmed = True
                skipped.append(aid)
                if not selected:
                    # 至少保留一个真实存在的聚合，避免目录退化成空目录。
                    selected = trial
        return selected, trimmed, skipped

    def _step_catalog(self) -> None:
        """v4：全量目录优先；装不下时 = 当前对象聚合闭包（必选） + 问句召回聚合。

        与 v3 的本质区别：
        - 注入单位从「35 个平行域」改为「聚合」（限界上下文内的一致性单元），
          seed 由当前对象所属聚合声明推出，不再靠「核心类恰好都在」的运气；
        - 跨聚合关系作为边界链接注入（端点带聚合名），永不静默丢弃——
          v3 在组内过滤处直接 continue，是「0 条关系」事故的根源；
        - 检索证据（五路通道、降级、零命中）随轨迹落盘，可审计。
        """
        full = self.sem.catalog()
        payload = full
        mode = full.get("mode") or "full"
        anchors: list[str] = []
        aggregate_ids: list[str] = []
        candidate_aggregates: list[str] = []
        seed_aggregates: list[str] = []
        question_aggregates: list[str] = []
        skipped_aggregates: list[str] = []
        search_result: dict = {}
        retrieval_evidence: dict = {}
        trimmed = False
        fallback_used = False
        budget = int(full.get("budget") or self.sem.catalog_budget())
        all_contexts = sorted(self.sem.contexts)
        if not full.get("fits"):
            # 当前对象所在聚合必选：该对象的类型、关系端点全在里面，
            # 缺了它目录会变成「一堆孤立类型 + 0 条关系」，规划无法进行。
            # 它是业务约束，不冒充检索结果；轨迹里单独列为 seed_aggregates。
            obj_agg = self.sem.aggregate_for_type(self.object_type)
            if obj_agg:
                seed_aggregates = [obj_agg]
            # 其余聚合完全由问句在全部上下文里召回的锚点相关性决定。
            search_result = self.sem.object_type_search(
                self.question, top_k=SEARCH_TOP_K,
            )
            anchors = [hit["id"] for hit in search_result.get("types") or []]
            retrieval_evidence = {
                "channels_used": search_result.get("channels_used") or [],
                "degraded": search_result.get("degraded") or [],
                "threshold": search_result.get("threshold"),
                "zero_hit": bool(search_result.get("zero_hit")),
            }
            question_aggregates = [
                a for a in self._aggregates_for_anchors(anchors)
                if a not in seed_aggregates
            ]
            candidate_aggregates = list(dict.fromkeys(
                seed_aggregates + question_aggregates))
            fallback_used = not question_aggregates
            aggregate_ids, trimmed, skipped_aggregates = (
                self._fit_aggregates_to_budget(candidate_aggregates, budget))
            if not aggregate_ids:
                # 当前对象不属于任何已声明聚合时，至少给一个真实存在的聚合。
                aggregate_ids = sorted(self.sem.aggregates)[:1]
            payload = self.sem.catalog(aggregate_ids=aggregate_ids)
            mode = "aggregate"

        items = self._catalog_items(payload, anchors)
        types = [cls["id"] for cls in payload.get("types") or []]
        fee_types = list(payload.get("fee_types") or [])
        boundary_links = list(payload.get("boundary_links") or [])
        self.catalog_mode = mode
        self.catalog_fits = bool(payload.get("fits"))
        self.catalog_group_ids = list(aggregate_ids)
        self.catalog_aggregate_ids = list(aggregate_ids)
        self.catalog_anchor_ids = list(anchors)
        self.catalog_context = self._catalog_context(
            payload, mode, anchors, aggregate_ids,
        )

        characters = int(payload.get("characters") or 0)
        budget = int(payload.get("budget") or self.sem.catalog_budget())
        relation_count = len(payload.get("relations") or [])
        group_count = len(payload.get("groups") or [])
        if mode == "full":
            expression = "ontology.catalog()"
            title = "全量紧凑 TBox 目录注入（不调用大模型）"
            conclusion = (
                f"全量紧凑目录 {characters} 字符，在 {budget} 字符预算内，"
                f"直接注入 {len(types)} 个类型和 {relation_count} 条关系；"
                "不预计算全局强依赖闭包。"
            )
            query_explain = (
                f"先尝试全量紧凑目录：只含类型 ID、名称、聚合归属和关系端点，"
                f"不返回对象实例。{characters} 字符未超出 {budget} 字符预算，"
                "因此不缩域、不做闭包，直接把目录交给模型。"
            )
        else:
            expression = (
                "ontology.catalog() → ontology.object_type_search("
                + json.dumps(self.question, ensure_ascii=False)
                + f", top_k={SEARCH_TOP_K}) → ontology.catalog(aggregate_ids="
                + json.dumps(aggregate_ids, ensure_ascii=False)
                + ")"
            )
            title = "必选对象聚合 + 按问句召回聚合（不调用大模型）"
            fallback_note = (
                "；问句零命中，目录只含必选对象聚合（显式标注，不冒充召回结果）"
                if fallback_used else ""
            )
            trim_note = (
                f"；预算内放不下而跳过 {len(skipped_aggregates)} 个聚合："
                f"{'、'.join(skipped_aggregates)}" if trimmed else ""
            )
            conclusion = (
                f"全量目录 {full.get('characters', 0)} 字符超出 {budget} 预算；"
                f"必选对象聚合 {'、'.join(seed_aggregates) or '—'}"
                "（当前对象的类型与关系端点所在，业务约束）；"
                f"在 {len(all_contexts)} 个上下文、{len(self.sem.aggregates)} 个聚合里"
                f"按问句召回 {len(anchors)} 个锚点，"
                f"追加 {len(question_aggregates)} 个问句聚合"
                f"{'、'.join(question_aggregates) or '—'}；"
                f"保留 {len(aggregate_ids)} 个聚合：{'、'.join(aggregate_ids) or '—'}；"
                f"注入 {len(types)} 个类型、{relation_count} 条关系"
                f"（含 {len(boundary_links)} 条边界链接）"
                f"{fallback_note}{trim_note}。"
            )
            query_explain = (
                f"全量目录装不下：当前对象所在聚合必选（含该对象的类型与关系端点，"
                f"缺了目录会只剩孤立类型且 0 条关系），它不冒充检索结果，"
                f"轨迹里单列为 seed_aggregates。其余聚合由 object_type_search 在全部 "
                f"{len(all_contexts)} 个上下文、{len(self.sem.aggregates)} 个聚合里"
                "按问句元数据打分召回的锚点决定；按顺序逐个加入目录并实测字符数，"
                "放不下的聚合跳过，不连坐后面的聚合"
                + ("；本次问句零命中，已显式标注" if fallback_used else "")
                + "。跨聚合关系作为边界链接注入（端点带聚合名），不静默丢弃。"
            )

        output_scope = {
            "types": types,
            "fee_types": fee_types,
            "aggregates": aggregate_ids or [g["id"] for g in payload.get("groups") or []],
            "groups": aggregate_ids or [g["id"] for g in payload.get("groups") or []],
            "anchors": anchors,
        }
        step = self._tbox_step(
            items, {"types": types, "fee_types": fee_types},
            {"reason": "v4：全量目录优先，超预算时按聚合缩域（边界链接不丢）"},
            len(self.steps) + 1,
        )
        step["title"] = title
        step["tag"] = "TBox · 紧凑目录"
        step["query"]["label"] = "先全量紧凑目录；超预算再聚合缩域 + 锚点检索"
        step["query"]["expression"] = expression
        step["query"]["api"] = (
            "semantic_layer.json · catalog(full_if_fits) + "
            "aggregates/contexts + object_type_search"
        )
        step["query"]["params"] = {
            "object_type": self.object_type,
            "question": self.question,
            "question_source": "default" if self.question_is_default else "user",
            "search_scope": (
                f"all_contexts({len(all_contexts)})" if mode != "full" else "full"
            ),
        }
        step["query"]["input"] = step["query"]["params"]
        step["query"]["scope"] = output_scope
        step["query"]["output"] = output_scope
        step["query"]["scope_label"] = "紧凑目录注入范围（不含对象实例）"
        step["query"]["input_explain"] = query_explain
        step["query"]["resolved"] = {
            "mode": mode,
            "fits": self.catalog_fits,
            "characters": characters,
            "budget": budget,
            "types": len(types),
            "relations": relation_count,
            "boundary_links": len(boundary_links),
            "groups": aggregate_ids or [
                group["id"] for group in payload.get("groups") or []
            ],
            "aggregates": aggregate_ids or [
                group["id"] for group in payload.get("groups") or []
            ],
            "anchors": anchors,
            "candidate_aggregates": candidate_aggregates,
            "seed_aggregates": seed_aggregates,
            "question_aggregates": question_aggregates,
            "skipped_aggregates": skipped_aggregates,
            # 兼容 v3 字段名（报告与旧测试读 seed_groups/question_groups）
            "seed_groups": seed_aggregates,
            "question_groups": question_aggregates,
            "search_scope": "all_contexts" if mode != "full" else "full",
            "trimmed_by_budget": trimmed,
            "fallback_used": fallback_used,
            "question_source": "default" if self.question_is_default else "user",
            "retrieval": retrieval_evidence,
        }
        step["layers"]["tbox"]["source"] = (
            "semantic_layer.json · 紧凑目录 + 聚合/上下文 + 对象类型检索"
        )
        step["layers"]["tbox"]["query"] = expression
        step["layers"]["tbox"]["query_explain"] = query_explain
        step["layers"]["tbox"]["note"] = (
            "装不下时：当前对象所在聚合必选（单独列为 seed_aggregates），其余聚合由"
            "问句在全量上下文里召回的锚点决定；逐个实测字符数，放不下的跳过；"
            "跨聚合关系作为边界链接注入。"
        )
        step["operations"] = [
            f"读取全量紧凑目录：{full.get('characters', 0)} 字符 / "
            f"预算 {budget} 字符",
            (
                "装得下：直接注入全量目录，不缩域、不做闭包"
                if mode == "full" else
                f"装不下：必选对象聚合 {'、'.join(seed_aggregates) or '—'} + "
                f"在 {len(all_contexts)} 个上下文里按问句召回 {len(anchors)} 个锚点 → "
                f"追加 {len(question_aggregates)} 个问句聚合 → "
                f"预算内保留 {len(aggregate_ids)} 个（{'、'.join(aggregate_ids) or '—'}）"
                + ("；零命中已标注" if fallback_used else "")
                + (f"；因超预算跳过 {'、'.join(skipped_aggregates)}" if trimmed else "")
            ),
            f"注入 {len(types)} 个类型、{relation_count} 条关系"
            f"（含 {len(boundary_links)} 条边界链接）、"
            f"{len(fee_types)} 个费用类型",
            "目录只含 ID、名称、聚合归属和关系端点，不含业务事实",
            f"按 target={self.object_type} 生成对象级能力集，共 {len(self.catalog)} 个",
        ]
        step["facts"] = [
            {"label": "目录模式",
             "value": "全量" if mode == "full" else "必选对象聚合 + 问句召回聚合"},
            {"label": "字符预算", "value": f"{characters}/{budget}"},
            {"label": "检索范围",
             "value": f"全部 {len(all_contexts)} 上下文" if mode != "full" else "全量目录"},
            {"label": "问题来源",
             "value": "默认问句（用户未提供）" if self.question_is_default else "用户输入"},
            {"label": "必选对象聚合",
             "value": "、".join(seed_aggregates) or "—"},
            {"label": "问句追加聚合",
             "value": "、".join(question_aggregates) or "—（零命中）"},
            {"label": "类型", "value": str(len(types))},
            {"label": "关系", "value": str(relation_count)},
            {"label": "边界链接", "value": str(len(boundary_links))},
            {"label": "聚合", "value": str(len(aggregate_ids) or group_count)},
            {"label": "候选聚合", "value": str(len(candidate_aggregates))},
            {"label": "锚点类型", "value": "、".join(anchors) or "—（零命中）"},
            {"label": "费用候选", "value": str(len(fee_types))},
            {"label": "可用能力", "value": str(len(self.catalog))},
            {"label": "业务事实", "value": "0 行（尚未查询）"},
        ]
        step["checks"] = [
            {"pass": bool(types), "label": "目录类型非空",
             "expected": "≥1 个类型", "actual": f"{len(types)} 个"},
            {"pass": bool(payload.get("relations")), "label": "目录关系非空",
             "expected": "≥1 条关系", "actual": f"{relation_count} 条"},
            {"pass": self.catalog_fits, "label": "目录在上下文预算内",
             "expected": f"≤{budget} 字符", "actual": f"{characters} 字符"},
            {"pass": True, "label": "本步未访问业务系统",
             "expected": "0 行业务事实", "actual": "0 行"},
        ]
        step["judgment"] = conclusion
        step["conclusion"] = conclusion
        step["next"] = (
            "由大模型基于紧凑目录规划最小充分类型子集，"
            "需要字段或规则时再按需读取 tbox.describe。"
        )
        step["source"] = "TBox：semantic_layer.json；ABox：未查询"
        step["basis"] = expression
        self.steps.append(step)
        self.pending = "plan"

    def _step_plan(self) -> None:
        plan = self._plan_tbox()
        self.planned = plan
        self.error = plan.get("_error")
        types = "、".join(plan["types"]) or "无"
        fees = "、".join(plan["fee_types"]) or "无"
        focus = "、".join(plan["focus_fee_types"]) or "无"
        task_lines = "\n".join(
            f"{i}. {t['intent']} → {t['tool_hint']}（{t['why']}）"
            for i, t in enumerate(plan["tasks"], 1)
        ) or "（无）"
        hypo_lines = "\n".join(
            f"{h['id']} {h['claim']}（验证：{h['verify_by']}）"
            for h in plan["hypotheses"]
        ) or "（无）"
        self.messages = [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (
                    f"业务问题：{self.question}\n"
                    f"诊断对象 object_id：{self.object_id}；"
                    f"运单业务主键 waybill_id：{self.waybill_id}。\n"
                    f"{self.catalog_context}\n"
                    f"规划器圈定的本体范围：类型 {types}；费用类型 {fees}；"
                    f"取证重点费用类型 {focus}。\n"
                    f"取证任务清单（执行阶段逐条对照，未执行要给出理由）：\n"
                    f"{task_lines}\n"
                    f"待验证假设（结论必须逐条回应）：\n{hypo_lines}\n"
                    "目录装得下时已直接注入全量紧凑目录；需要字段、规则或实例时，"
                    "再按需调用 tbox.describe 或 ontology.search_around。"
                ),
            },
        ]
        self.pending = "tbox"
        step = self._llm_step(
            index=len(self.steps) + 1,
            title="大模型规划本体范围（调用 LLM）",
            tag="LLM · 本体规划",
            messages=plan.get("_messages") or [],
            output=plan.get("_output") or "",
            parsed={
                "types": plan["types"],
                "fee_types": plan["fee_types"],
                "focus_fee_types": plan["focus_fee_types"],
                "focus_reason": plan.get("focus_reason") or "",
                "tasks": plan["tasks"],
                "hypotheses": plan["hypotheses"],
                "reason": plan.get("reason") or "",
            },
            thought=plan.get("reason") or "",
            ok=not plan.get("_error"),
            error=plan.get("_error"),
            elapsed_ms=plan.get("_elapsed_ms", 0),
            conclusion=(
                f"规划类型：{types}；费用类型：{fees}；"
                f"取证重点：{focus}；任务 {len(plan['tasks'])} 条；"
                f"假设 {len(plan['hypotheses'])} 条"
            ),
            next_text=f"查询本体定义：{types}",
            thinking=False,
        )
        # 规划质量可度量：裁剪率 + 任务/假设计数 + 工具合法性
        catalog_fee_count = len(self.sem.pricing)
        focus_count = len(plan["focus_fee_types"])
        tools_ok = all(t["tool_hint"] in self.catalog for t in plan["tasks"])
        step["facts"] = [
            {"label": "规划类型", "value": f"{len(plan['types'])} 个"},
            {"label": "声明费用范围", "value": f"{len(plan['fee_types'])} 种"},
            {"label": "取证重点", "value": f"{focus_count} / {catalog_fee_count} 种"
                                        f"（裁剪率 {round(100 * (1 - focus_count / max(catalog_fee_count, 1)))}%）"},
            {"label": "取证任务", "value": f"{len(plan['tasks'])} 条"},
            {"label": "待验证假设", "value": f"{len(plan['hypotheses'])} 条"},
        ]
        step["checks"] = [
            {"pass": focus_count > 0, "label": "取证重点非空",
             "expected": "≥1 种", "actual": f"{focus_count} 种"},
            {"pass": focus_count < catalog_fee_count or catalog_fee_count <= 1,
             "label": "取证重点已裁剪（不是全部费用类型）",
             "expected": f"<{catalog_fee_count} 种",
             "actual": f"{focus_count} 种"},
            {"pass": bool(plan["tasks"]), "label": "取证任务清单非空",
             "expected": "≥1 条", "actual": f"{len(plan['tasks'])} 条"},
            {"pass": tools_ok, "label": "任务工具都在可用能力集内",
             "expected": "全部合法", "actual": "合法" if tools_ok else "存在越界工具"},
            {"pass": bool(plan["hypotheses"]), "label": "假设清单非空",
             "expected": "≥1 条", "actual": f"{len(plan['hypotheses'])} 条"},
        ]
        # 注意挂到 step["data"]["datasets"]：前端 renderChainData 只读这个键
        step["data"]["datasets"] = [
            {
                "caption": "取证任务清单（执行阶段逐条对照）",
                "columns": [
                    {"key": "no", "label": "#"},
                    {"key": "intent", "label": "意图"},
                    {"key": "tool_hint", "label": "建议工具"},
                    {"key": "why", "label": "理由"},
                ],
                "rows": [{"no": i, **t} for i, t in enumerate(plan["tasks"], 1)],
                "note": "任务来自规划器；执行对照见结论阶段的「计划执行对照」",
            },
            {
                "caption": "待验证假设（结论必须逐条回应）",
                "columns": [
                    {"key": "id", "label": "编号"},
                    {"key": "claim", "label": "可证伪判断"},
                    {"key": "verify_by", "label": "验证依据"},
                ],
                "rows": plan["hypotheses"],
                "note": "假设驱动取证：每条假设在结论里给出 成立/排除/待补证",
            },
        ]
        self.steps.append(step)

    def _step_tbox(self) -> None:
        plan = self.planned
        scope = self.sem.describe(plan["types"], plan["fee_types"])
        items, query_inputs = self._tbox_items(
            "", [], {}, scope={
                "types": [cls["id"] for cls in scope["classes"]],
                "fee_types": list(scope["pricing"].keys()),
            },
        )
        self.steps.append(self._tbox_step(
            items, query_inputs, plan, len(self.steps) + 1
        ))
        self.steps[-1]["title"] = "本体定义查询 tbox.describe（不调用大模型）"
        self.steps[-1]["tag"] = "TBox · 定义"
        self.messages.append({
            "role": "user",
            "content": (
                self._tbox_context(items)
                + "\n以上只包含本次圈定范围的 TBox 定义；"
                "需要其他类型时再调用 tbox.describe。"
            ),
        })
        self.pending = "decide"

    def _step_tbox_query(self) -> None:
        """模型按需调用 tbox.describe：只加载被点名的类型定义。"""
        pending = self.pending_action or {}
        arguments = pending.get("arguments") or {}
        types = list(arguments.get("types") or [])
        fees = list(arguments.get("fee_types") or [])
        scope = self.sem.describe(types, fees)
        items, query_inputs = self._tbox_items(
            "", [], {}, scope={
                "types": [cls["id"] for cls in scope["classes"]],
                "fee_types": list(scope["pricing"].keys()),
            },
        )
        step = self._tbox_step(
            items, query_inputs,
            {"reason": "大模型在诊断过程中按需调用 tbox.describe"},
            len(self.steps) + 1,
        )
        step["title"] = "按需本体定义查询 tbox.describe（不调用大模型）"
        step["tag"] = "TBox · 按需"
        step["layers"]["tbox"]["note"] = (
            "由大模型主动调用 tbox.describe；只返回被点名的类型，"
            "不再整份 TBox 灌入上下文。"
        )
        step["operations"] = [
            f"大模型提出本体查询：{'、'.join(query_inputs['types']) or '无'}",
            f"调用 tbox.describe() 按需加载 {len(query_inputs['types'])} 个类型",
            "只把本次需要的定义注入上下文，不加载全量 TBox",
        ]
        step["conclusion"] = (
            f"本步按需加载 {len(items)} 项定义，不含业务事实；"
            "模型将带着这份定义范围继续查 ABox。"
        )
        step["next"] = "回到大模型，基于新增定义决定下一步业务查询。"
        self.steps.append(step)
        self.tbox_queries += 1
        self.messages.append({
            "role": "user",
            "content": (
                f"第 {len(self.steps)} 步 tbox.describe 返回：\n"
                + self._tbox_context(items)
            ),
        })
        self.pending_action = None
        self.pending = "decide"

    def _step_object_type_search(self) -> None:
        """模型按需调用 ontology.object_type_search，召回锚点类型。"""
        pending = self.pending_action or {}
        arguments = pending.get("arguments") or {}
        question = str(arguments.get("question") or self.question).strip()
        raw_groups = arguments.get("group_ids")
        group_ids = [
            str(g) for g in (raw_groups if isinstance(raw_groups, list) else [])
            if str(g) in self.sem.object_type_group_map
        ]
        try:
            top_k = int(arguments.get("top_k") or SEARCH_TOP_K)
        except (TypeError, ValueError):
            top_k = SEARCH_TOP_K
        top_k = max(1, min(top_k, 20))
        result = self.sem.object_type_search(
            question, top_k=top_k, group_ids=group_ids or None,
        )
        items: list[dict] = []
        for hit in result.get("types") or []:
            groups = "、".join(hit.get("groups") or [])
            items.append({
                "kind": "class",
                "id": hit["id"],
                "label": hit.get("label", hit["id"]),
                "detail": (
                    f'分数 {hit.get("score", 0)} · 命中 '
                    f'{"/".join(hit.get("matched_fields") or []) or "—"}'
                    f' · 业务域 {groups or "—"}'
                ),
            })
        for group in result.get("groups") or []:
            items.append({
                "kind": "group",
                "id": group["id"],
                "label": group.get("label", group["id"]),
                "detail": (
                    f'分数 {group.get("score", 0)} · '
                    f'{len(group.get("types") or [])} 个类型'
                ),
            })
        types = [hit["id"] for hit in result.get("types") or []]
        expression = (
            f'ontology.object_type_search({json.dumps(question, ensure_ascii=False)}, '
            f'top_k={top_k}'
            + (f', group_ids={json.dumps(group_ids, ensure_ascii=False)}'
               if group_ids else "")
            + ")"
        )
        step = self._tbox_step(
            items, {"types": types, "fee_types": []},
            {"reason": "大模型在诊断过程中按需检索对象类型锚点"},
            len(self.steps) + 1,
        )
        step["title"] = "对象类型锚点检索 ontology.object_type_search（不调用大模型）"
        step["tag"] = "TBox · 对象类型检索"
        step["query"]["label"] = "自然语言问题 → 对象类型元数据 → 锚点类型"
        step["query"]["expression"] = expression
        step["query"]["api"] = "semantic_layer.json · object_type_search"
        step["query"]["resolved"] = {
            "matches": len(types),
            "groups": [group["id"] for group in result.get("groups") or []],
            "scope": group_ids,
        }
        step["layers"]["tbox"]["source"] = "semantic_layer.json · 对象类型元数据"
        step["layers"]["tbox"]["query"] = expression
        step["layers"]["tbox"]["note"] = (
            "只按类型 ID、名称、描述、别名和业务域做元数据匹配，"
            "不查对象实例，也不沿关系做全局闭包。"
        )
        step["operations"] = [
            f"检索问题：{question}",
            f"检索范围：{'、'.join(group_ids) or '全部业务域'}",
            f"召回 {len(types)} 个锚点类型：" + ("、".join(types) or "无"),
            "下一步可按需调用 tbox.describe 读取定义，"
            "或直接对锚点对象调用 ontology.search_around",
        ]
        step["facts"] = [
            {"label": "检索问题", "value": self._clip(question, 60)},
            {"label": "检索范围", "value": "、".join(group_ids) or "全部业务域"},
            {"label": "锚点类型", "value": "、".join(types) or "无"},
            {"label": "业务事实", "value": "0 行（尚未查询）"},
        ]
        step["checks"] = [
            {"pass": bool(types), "label": "召回锚点类型",
             "expected": "≥1 个类型", "actual": f"{len(types)} 个"},
            {"pass": True, "label": "本步未访问业务系统",
             "expected": "0 行业务事实", "actual": "0 行"},
        ]
        step["conclusion"] = (
            f"按对象类型元数据召回 {len(types)} 个锚点类型"
            + (f"：{'、'.join(types)}" if types else "，未命中。")
        )
        step["next"] = (
            "回到大模型，基于锚点类型决定读取定义还是沿 link type 做 search-around。"
        )
        self.steps.append(step)
        self.tbox_queries += 1
        self.messages.append({
            "role": "user",
            "content": (
                f"第 {len(self.steps)} 步 ontology.object_type_search 结果：\n"
                + self._compact({
                    "question": question,
                    "group_ids": group_ids,
                    "types": result.get("types") or [],
                    "groups": result.get("groups") or [],
                })
            ),
        })
        self.pending_action = None
        self.pending = "decide"

    def _step_tbox_search(self) -> None:
        """模型按需调用 schema search：种子召回 + 强依赖闭包。"""
        pending = self.pending_action or {}
        arguments = pending.get("arguments") or {}
        query = str(arguments.get("question") or "").strip()
        raw_types = arguments.get("types")
        types = [
            str(t) for t in (raw_types if isinstance(raw_types, list) else [])
            if str(t) in self.sem.classes
        ]
        try:
            top_k = int(arguments.get("top_k") or SEARCH_TOP_K)
        except (TypeError, ValueError):
            top_k = SEARCH_TOP_K
        top_k = max(1, min(top_k, 50))
        try:
            depth = int(arguments.get("depth", SCHEMA_DEPTH))
        except (TypeError, ValueError):
            depth = SCHEMA_DEPTH
        depth = max(0, min(depth, 4))
        found = self.sem.schema(
            question=query or None,
            types=types or None,
            top_k=top_k,
            depth=depth,
            fee_top_k=SEARCH_FEE_TOP_K,
            closure="strong",
        )
        closure = found.get("closure") or {}
        whitelist_count = len(closure.get("relation_ids") or [])
        excluded_count = len(closure.get("excluded_relation_ids") or [])
        all_types = [cls["type"] for cls in found["classes"]]
        fee_types = list(found["fee_types"])
        items: list[dict] = []
        for cls in found["classes"]:
            props = cls.get("properties") or []
            detail = (
                f'主键 {cls.get("key") or "—"} · 属性 '
                + " / ".join(map(str, props[:6]))
            )
            if len(props) > 6:
                detail += f" 等 {len(props)} 项"
            detail += f' · {cls.get("reason", "邻接闭包")}'
            items.append({
                "kind": "class", "id": cls["type"],
                "label": cls["label"], "detail": detail,
            })
        for rel in found["relations"]:
            detail = (
                f'{rel["domain"]} → {rel["range"]} · '
                + ("闭包边界" if rel.get("boundary") else "两端均在范围内")
            )
            items.append({
                "kind": "relation", "id": rel["id"],
                "label": rel.get("label", rel["id"]), "detail": detail,
            })
        for fee_type in fee_types:
            items.append({
                "kind": "pricing", "id": f"pricing.{fee_type}",
                "label": fee_type, "detail": "按问题召回的费用类型",
            })
        self.catalog_index = items
        expression = (
            f'ontology.schema({json.dumps(query or self.question, ensure_ascii=False)}, '
            f'top_k={top_k}, depth={depth})'
        )
        step = self._tbox_step(
            items, {"types": all_types, "fee_types": fee_types},
            {"reason": "大模型在诊断过程中按需调用 schema search"},
            len(self.steps) + 1,
        )
        step["title"] = "按需 schema search + 强依赖闭包（不调用大模型）"
        step["tag"] = "TBox · Schema 聚焦"
        step["query"]["label"] = "自然语言问题 → schema search → 强依赖闭包"
        step["query"]["expression"] = expression
        step["query"]["api"] = (
            "semantic_layer.json · schema(question, top_k, depth)")
        step["query"]["resolved"] = {
            "depth": depth,
            "closure_mode": closure.get("mode", "strong"),
            "closure_relations": whitelist_count,
            "excluded_relations": excluded_count,
            "types": len(all_types),
            "relations": len(found["relations"]),
        }
        step["layers"]["tbox"]["source"] = "semantic_layer.json · schema search + 强依赖闭包"
        step["layers"]["tbox"]["query"] = expression
        step["layers"]["tbox"]["note"] = (
            "由大模型主动调用 schema search；种子类型只沿强依赖白名单扩展，"
            f"白名单 {whitelist_count} 条，排除 {excluded_count} 条弱关联，"
            "结构性依赖不会被 top-k 截断，也不会被弱关联拖成全图。"
        )
        step["operations"] = [
            f"大模型提出检索问题：{query or '（按类型读取）'}",
            f"调用 ontology.schema 检索 top-{top_k} 种子类型",
            f"沿强依赖白名单扩展 {depth} 跳（白名单 {whitelist_count} 条，"
            f"排除 {excluded_count} 条弱关联）",
            "需要完整定义时再调用 tbox.describe",
        ]
        step["facts"] = [f for f in step["facts"] if f["label"] != "类定义"]
        for fact in step["facts"]:
            if fact["label"] == "圈定类型":
                fact["label"] = "schema 类型"
            elif fact["label"] == "定价参数":
                fact["label"] = "费用候选"
        for check in step["checks"]:
            if check["label"] == "圈定类型非空":
                check["label"] = "schema 类型非空"
        step["conclusion"] = (
            f"本步经 schema search + {depth} 跳强依赖闭包得到 {len(all_types)} 个类型、"
            f"{len(fee_types)} 个费用类型，只走白名单 {whitelist_count} 条关系；"
            "模型可据此继续读取定义或查询事实。"
        )
        step["next"] = "回到大模型，基于闭包结果决定下一步。"
        self.steps.append(step)
        self.tbox_queries += 1
        self.messages.append({
            "role": "user",
            "content": (
                f"第 {len(self.steps)} 步 schema search 闭包结果：\n"
                + self._tbox_context(items)
                + "\n以上是种子类型只沿强依赖白名单扩展后的范围；需要字段、关系或规则时调用 tbox.describe。"
            ),
        })
        self.pending_action = None
        self.pending = "decide"

    def _step_tbox_path(self) -> None:
        """模型按需调用 ontology.path：查询两个类型之间的合法关系路径。"""
        pending = self.pending_action or {}
        arguments = pending.get("arguments") or {}
        from_type = str(arguments.get("from_type") or "").strip()
        to_type = str(arguments.get("to_type") or "").strip()
        try:
            max_depth = int(arguments.get("max_depth") or 4)
        except (TypeError, ValueError):
            max_depth = 4
        try:
            limit = int(arguments.get("limit") or 10)
        except (TypeError, ValueError):
            limit = 10
        error: str | None = None
        paths: list[dict] = []
        try:
            paths = self.sem.paths(
                from_type, to_type,
                max_depth=max_depth, limit=limit,
            )
        except Exception as exc:
            error = str(exc)
        relation_items: dict[str, dict] = {}
        for path in paths:
            for index, link in enumerate(path["steps"], start=1):
                relation_items.setdefault(link["predicate"], {
                    "kind": "relation",
                    "id": link["predicate"],
                    "label": link["label"],
                    "detail": (
                        f'{link["domain"]} → {link["range"]} · '
                        f'{link["direction"]} · 路径第 {index} 步'
                    ),
                })
        items = list(relation_items.values())
        expression = (
            f'ontology.path({json.dumps(from_type, ensure_ascii=False)}, '
            f'{json.dumps(to_type, ensure_ascii=False)}, '
            f'max_depth={max_depth})'
        )
        step = self._tbox_step(
            items, {"types": [from_type, to_type], "fee_types": []},
            {"reason": "大模型在诊断过程中按需查询类型路径"},
            len(self.steps) + 1,
        )
        step["title"] = "按需本体路径查询 ontology.path（不调用大模型）"
        step["tag"] = "TBox · 路径"
        step["query"]["label"] = "起止类型 → 合法关系路径"
        step["query"]["expression"] = expression
        step["query"]["api"] = "semantic_layer.json · paths(from_type, to_type)"
        step["layers"]["tbox"]["source"] = "semantic_layer.json · 关系图路径"
        step["layers"]["tbox"]["query"] = expression
        step["layers"]["tbox"]["note"] = (
            "path 只返回关系定义上的合法路径，不查任何实例。"
        )
        step["operations"] = [
            f"起点类型：{from_type or '未指定'}",
            f"终点类型：{to_type or '未指定'}",
            f"最大深度：{max_depth}",
            f"返回 {len(paths)} 条合法路径",
        ]
        if error:
            step["ok"] = False
            step["conclusion"] = f"路径查询失败：{error}"
            step["next"] = "回到大模型，根据错误信息修正类型或深度。"
        else:
            step["conclusion"] = (
                f"在 {from_type} 与 {to_type} 之间找到 {len(paths)} 条合法路径"
                f"（最大深度 {max_depth}）。"
            )
            step["next"] = "回到大模型，基于路径决定查询哪些实例关系。"
        self.steps.append(step)
        self.tbox_queries += 1
        self.messages.append({
            "role": "user",
            "content": (
                f"第 {len(self.steps)} 步 ontology.path 结果：\n"
                + self._compact({
                    "from_type": from_type,
                    "to_type": to_type,
                    "paths": paths,
                    "error": error,
                })
            ),
        })
        self.pending_action = None
        self.pending = "decide"

    def _step_decide(self) -> None:
        if len(self.observations) + self.tbox_queries >= self.max_steps:
            self._step_exhausted()
            return
        # 知识型问题：声明已到手就本地收口。describe 结果里已经带着答案，
        # 再把决策交给 LLM 只会赌它的 JSON 输出格式（实测常见直接文本作答）。
        if (self.question_kind == "knowledge"
                and any(it.get("kind") in ("rule", "pricing", "action")
                        for it in self.tbox_seen.values())):
            self._knowledge_final()
            return
        self._sync_task_reminder()
        started = time.perf_counter()
        try:
            raw = self.client.chat(self.messages)
        except LLMError as exc:
            self.error = str(exc)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        action = self._parse_action(raw)
        if action is None:
            self.error = "大模型输出无法解析"
            raise LLMError("大模型输出无法解析，未执行任何工具")
        self.error = None
        self.messages.append({"role": "assistant", "content": raw[:4000]})
        if action.get("action") == "final":
            self.final_action = action
            if self.client.final_thinking and self.observations:
                self.pending = "review"
                next_text = "调用大模型复核结论"
            else:
                self.pending = "action"
                next_text = "生成动作提案（不调用大模型）"
            conclusion = "模型认为信息足够，给出最终结论。"
            thought = action.get("thought") or ""
        else:
            tool = str(action.get("tool") or "").strip().strip("`")
            arguments = self._normalize_arguments(action.get("arguments"))
            if tool.lower() in {"ontology.schema"}:
                query = str(arguments.get("question") or "").strip()
                raw_types = arguments.get("types")
                types = [
                    str(t) for t in (raw_types if isinstance(raw_types, list) else [])
                    if str(t) in self.sem.classes
                ]
                if not query and not types:
                    self.pending = "decide"
                    conclusion = (
                        f"{tool} 参数无效：question/types 为空，未执行检索。"
                    )
                    next_text = "重新调用大模型决策"
                else:
                    self.pending_action = {
                        "action": action,
                        "tool": "ontology.schema",
                        "arguments": {
                            "question": query,
                            "types": types,
                            "top_k": arguments.get("top_k") or SEARCH_TOP_K,
                            "depth": arguments.get("depth", SCHEMA_DEPTH),
                        },
                    }
                    self.pending = "tbox_search"
                    next_text = (
                        f"schema 聚焦：{query}" if query
                        else f"schema 聚焦：{'、'.join(types) or '全量'}"
                    )
                    conclusion = "模型决定做 schema search + 邻接闭包。"
            elif tool.lower() in {
                "ontology.object_type_search", "object_type_search",
                "tbox.object_type_search",
            }:
                question = str(arguments.get("question") or "").strip()
                raw_groups = arguments.get("group_ids")
                group_ids = [
                    str(g) for g in (
                        raw_groups if isinstance(raw_groups, list) else []
                    ) if str(g) in self.sem.object_type_group_map
                ]
                if not question:
                    self.pending = "decide"
                    conclusion = (
                        "ontology.object_type_search 参数无效：question 为空，"
                        "未执行检索。"
                    )
                    next_text = "重新调用大模型决策"
                else:
                    self.pending_action = {
                        "action": action,
                        "tool": "ontology.object_type_search",
                        "arguments": {
                            "question": question,
                            "top_k": arguments.get("top_k") or SEARCH_TOP_K,
                            "group_ids": group_ids,
                        },
                    }
                    self.pending = "object_type_search"
                    next_text = f"检索对象类型锚点：{question}"
                    conclusion = "模型决定按对象类型元数据检索锚点类型。"
            elif tool.lower() in {"ontology.path", "tbox.path", "tbox_path"}:
                from_type = str(arguments.get("from_type") or "").strip()
                to_type = str(arguments.get("to_type") or "").strip()
                allowed = from_type in self.sem.classes and to_type in self.sem.classes
                if not allowed:
                    self.pending = "decide"
                    conclusion = (
                        "ontology.path 参数无效："
                        "from_type/to_type 必须是已定义类型，未执行查询。"
                    )
                    next_text = "重新调用大模型决策"
                else:
                    self.pending_action = {
                        "action": action,
                        "tool": "ontology.path",
                        "arguments": {
                            "from_type": from_type,
                            "to_type": to_type,
                            "max_depth": arguments.get("max_depth") or 4,
                            "limit": arguments.get("limit") or 10,
                        },
                    }
                    self.pending = "tbox_path"
                    next_text = f"查询类型路径：{from_type} → {to_type}"
                    conclusion = "模型决定查询两个类型之间的合法关系路径。"
            elif tool.lower() in {"tbox.describe", "tbox_describe", "describe"}:
                raw_types = arguments.get("types")
                raw_fees = arguments.get("fee_types")
                types = [
                    str(t) for t in (raw_types if isinstance(raw_types, list) else [])
                    if str(t) in self.sem.classes
                ]
                fees = [
                    str(f) for f in (raw_fees if isinstance(raw_fees, list) else [])
                    if str(f) in self.sem.pricing
                ]
                if not types and not fees:
                    self.pending = "decide"
                    conclusion = (
                        "tbox.describe 参数无效：types/fee_types 为空或不在目录中，"
                        "未执行查询。"
                    )
                    next_text = "重新调用大模型决策"
                else:
                    self.pending_action = {
                        "action": action,
                        "tool": "tbox.describe",
                        "arguments": {"types": types, "fee_types": fees},
                    }
                    self.pending = "tbox_query"
                    next_text = (
                        f'读取本体定义：{"、".join(types) or "费用类型"}'
                    )
                    conclusion = "模型决定按需读取本体定义。"
            else:
                matched = next(
                    (name for name in self.catalog
                     if name.lower() == tool.lower()),
                    None,
                )
                tool = matched or tool
                self.pending_action = {
                    "action": action, "tool": tool, "arguments": arguments,
                }
                self.pending = "tool"
                next_text = (
                    f"执行 MCP 业务事实查询：{tool}"
                    f"（{json.dumps(arguments, ensure_ascii=False)}）"
                )
                conclusion = f"模型决定调用 {tool} 获取业务事实。"
            thought = action.get("thought") or ""
        self.steps.append(self._llm_step(
            index=len(self.steps) + 1,
            title=f'大模型决策：{action.get("title") or "下一步动作"}',
            tag="LLM · 决策",
            messages=self.messages[:-1],
            output=raw,
            parsed=action,
            thought=thought,
            ok=True,
            error=None,
            elapsed_ms=elapsed_ms,
            conclusion=conclusion,
            next_text=next_text,
        ))

    def _step_tool(self) -> None:
        pending = self.pending_action or {}
        action = pending.get("action") or {}
        tool = str(pending.get("tool") or "")
        arguments = pending.get("arguments") or {}
        step, observation = self._run_tool_step(
            action, tool, arguments, len(self.steps) + 1
        )
        self.steps.append(step)
        self.observations.append(observation)
        self.messages.append({
            "role": "user",
            "content": f"第 {len(self.steps)} 步观察结果：{self._compact(observation)}",
        })
        self.pending_action = None
        self.pending = "decide"

    def _step_review(self) -> None:
        hypo_lines = "\n".join(
            f"{h['id']} {h['claim']}" for h in self.planned.get("hypotheses", [])
        )
        if self.question_kind == "knowledge":
            evidence_rule = (
                "\n这是知识型问题：最终答案必须引用对话中已出现的 TBox 声明——"
                "规则 ID、公式、定价参数值（如免费分钟数、每块单价）、"
                "状态机迁移。凡声明已在上下文中的假设，必须给出参数值并判"
                "「成立」，禁止标待补证；只有上下文确实没有的声明才允许待补证。"
            )
        else:
            evidence_rule = (
                "\n已核对的 TBox 声明与 ABox 观察同样是证据：规则公式、定价参数、"
                "状态机迁移可直接引用其 ID 与参数值作答，不要把已有声明标成待补证。"
            )
        instruction = (
            "请复核以上全部观察，核对金额、规则和证据引用是否一致；"
            "如有遗漏或错误请修正。只输出 final JSON，不要调用工具："
            '{"action":"final","title":"结论","question":"最终核对的问题",'
            '"thought":"综合判断","answer":"最终结论"}\n'
            "answer 必须包含三段：① 逐条回应以下假设（成立/排除/待补证+证据）：\n"
            + (hypo_lines or "（无假设）")
            + "\n② 已核对的事实；③ 未决项（缺什么、建议找谁、哪个系统）。"
            + evidence_rule
        )
        self.messages.append({"role": "user", "content": instruction})
        started = time.perf_counter()
        try:
            raw = self.client.chat(
                self.messages, thinking=self.client.final_thinking
            )
        except LLMError as exc:
            self.error = str(exc)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        candidate = self._parse_action(raw)
        if not candidate or candidate.get("action") != "final":
            self.error = "复核输出无法解析"
            raise LLMError("复核输出没有给出合法 final 结论")
        self.final_action = candidate
        self.messages.append({"role": "assistant", "content": raw[:4000]})
        self.reviewed = True
        self.pending = "action"
        self.steps.append(self._llm_step(
            index=len(self.steps) + 1,
            title="大模型复核结论",
            tag="LLM · 复核",
            messages=self.messages,
            output=raw,
            parsed=candidate,
            thought=candidate.get("thought") or "",
            ok=True,
            error=None,
            elapsed_ms=elapsed_ms,
            conclusion="复核完成，结论已更新。",
            next_text="生成动作提案（不调用大模型）",
            thinking=self.client.final_thinking,
        ))

    def _degraded_final(self, raw: str) -> dict:
        """工具预算用尽且模型没给合法 final 时的降级结论。

        只用**已经取到的观察**作答，不臆造数字。收口是三段式：
        已核对什么 / 未决项（缺什么、找谁、哪个系统）/ 假设回应到哪一步——
        "没查完"必须是结构化清单，不能是一句"尚未完成全部交叉核对"。
        """
        lines = []
        for obs in self.observations:
            tool = obs.get("tool") or "查询"
            summary = obs.get("summary") or obs.get("result") or ""
            if isinstance(summary, (dict, list)):
                summary = self._compact(summary, 160)
            lines.append(f"- {tool}：{str(summary)[:160]}")
        body = "\n".join(lines[:8]) or "（本轮没有取到任何观察）"
        # 降级也不能丢 TBox：知识型问题的答案往往在已查到的声明里
        # （规则公式、定价参数、动作权限），它们不在 observations 里。
        decl_lines = [
            f"- {item['kind']}|{item['id']}：{str(item.get('detail'))[:200]}"
            for item in self.tbox_seen.values()
            if item.get("kind") in ("rule", "pricing", "action")
        ][:8]
        decl_body = "\n".join(decl_lines)
        open_items = self._open_items()
        open_lines = [
            f"{i}. {item['missing']} → 建议 {item['owner']}（{item['system']}）"
            f"；阻塞：{item['blocks']}"
            for i, item in enumerate(open_items, 1)
        ]
        hypo_lines = [
            f"- {h['id']} {h['claim']}：{h['verdict']}"
            + (f"（{h['evidence']}）" if h.get("evidence") else "")
            for h in self._hypothesis_verdicts()
        ]
        parts = [
            "**初步结论**（观察不足，非最终判定）：\n"
            "**已核对**（本轮取到的观察）：\n" + body,
        ]
        if decl_body:
            parts.append("**已核对的 TBox 声明**（规则 / 定价参数 / 动作）：\n"
                         + decl_body)
        if hypo_lines:
            parts.append("**假设回应**：\n" + "\n".join(hypo_lines))
        parts.append(
            "**未决项**：\n" + ("\n".join(open_lines) if open_lines
                              else "1. 无——本轮任务清单已全部执行")
        )
        return {
            "action": "final",
            "title": "初步结论（工具预算用尽，未完成完整核对）",
            "question": self.question,
            "thought": "工具调用次数已用完，模型未给出结构化结论，改用已有观察归纳。",
            "answer": "\n\n".join(parts),
            "degraded": True,
        }

    def _knowledge_final(self) -> None:
        """知识型问题：以已取到的 TBox 声明为证据，本地确定性收口。

        不是降级——是设计路径：答案就在 rule / pricing / action 声明里，
        声明到手后无需再调决策 LLM，也就不赌它的输出格式。
        """
        decl_lines = [
            f"- {it['kind']}|{it['id']}（{it.get('label') or ''}）："
            f"{str(it.get('detail') or '')[:400]}"
            for it in self.tbox_seen.values()
            if it.get("kind") in ("class", "rule", "pricing", "action", "function")
        ][:12]
        hypo_lines = [
            f"- {h['id']} {h['claim']}：{h['verdict']}"
            + (f"（{h['evidence']}）" if h.get("evidence") else "")
            for h in self._hypothesis_verdicts()
        ]
        parts = ["**知识问答结论**（证据 = 本体声明，确定性收口）："]
        if decl_lines:
            parts.append("**已核对的声明**：\n" + "\n".join(decl_lines))
        if hypo_lines:
            parts.append("**假设回应**：\n" + "\n".join(hypo_lines))
        if len(parts) == 1:
            parts.append("（未取到与问题相关的声明，请扩大 describe 范围）")
        self.final_action = {
            "action": "final",
            "title": "知识问答结论（本体声明）",
            "question": self.question,
            "thought": "知识型问题：答案在 TBox 声明中，已取到即本地收口。",
            "answer": "\n\n".join(parts),
        }
        self._finish()

    # ---------------------------------------------------------------- 计划驱动取证
    #
    # 规划产出 tasks（取证任务清单）后，执行阶段必须可对照：
    #   - _sync_task_reminder：每次决策前把"还没做的任务"注入模型上下文，
    #     规划真正驱动执行（原来规划只喂给 tbox.describe，工具调用是临场发挥）
    #   - _task_for_tool：工具执行时标记服务了哪条任务；计划外的显式标注
    #   - _plan_execution_review：结论阶段出对照表（已执行/未执行+理由/计划外）
    TASK_REMINDER_TAG = "[任务提醒]"

    def _covered_task_tools(self) -> set[str]:
        return {
            obs["tool"] for obs in self.observations
            if obs.get("serves")
        }

    def _uncovered_tasks(self) -> list[dict]:
        covered = self._covered_task_tools()
        return [t for t in self.planned.get("tasks", [])
                if t["tool_hint"] not in covered]

    def _sync_task_reminder(self) -> None:
        """把未覆盖任务写进模型上下文（替换上一条提醒，不累积）。"""
        self.messages = [
            m for m in self.messages
            if not str(m.get("content") or "").startswith(self.TASK_REMINDER_TAG)
        ]
        pending = self._uncovered_tasks()
        if not pending:
            return
        lines = [
            f"{i}. {t['intent']} → {t['tool_hint']}（目标 {t['target']}）"
            for i, t in enumerate(pending, 1)
        ]
        self.messages.append({
            "role": "user",
            "content": (
                f"{self.TASK_REMINDER_TAG}规划里的取证任务还没做完，"
                f"请优先逐条完成（预算内）：\n" + "\n".join(lines)
            ),
        })

    def _task_for_tool(self, tool: str) -> dict | None:
        for task in self.planned.get("tasks", []):
            if task["tool_hint"] == tool and tool not in self._covered_task_tools():
                return task
        return None

    def _plan_execution_review(self) -> list[dict]:
        """计划 vs 执行对照表：每条任务一个状态，未执行给理由，计划外显式列出。"""
        covered = self._covered_task_tools()
        rows = []
        for task in self.planned.get("tasks", []):
            hits = [o for o in self.observations
                    if o.get("tool") == task["tool_hint"] and o.get("serves")]
            if hits:
                rows.append({
                    "intent": task["intent"],
                    "tool": task["tool_hint"],
                    "status": "已执行",
                    "evidence": f"{len(hits)} 次调用 · "
                                f"{sum(h.get('rows', 0) for h in hits)} 行",
                    "reason": "—",
                })
            elif task["tool_hint"] in {o.get("tool") for o in self.observations}:
                rows.append({
                    "intent": task["intent"],
                    "tool": task["tool_hint"],
                    "status": "计划外执行",
                    "evidence": "被调用但未标记服务本任务",
                    "reason": "模型选择了其他意图",
                })
            else:
                reason = ("工具预算用尽，未轮到" if self.degraded
                          else "模型判断信息已足够，未执行")
                rows.append({
                    "intent": task["intent"],
                    "tool": task["tool_hint"],
                    "status": "未执行",
                    "evidence": "—",
                    "reason": reason,
                })
        planned_tools = {t["tool_hint"] for t in self.planned.get("tasks", [])}
        for tool in sorted({o.get("tool") for o in self.observations
                            if o.get("tool")} - planned_tools):
            hits = [o for o in self.observations if o.get("tool") == tool]
            rows.append({
                "intent": "（计划外）模型自主追加的查询",
                "tool": tool,
                "status": "计划外执行",
                "evidence": f"{len(hits)} 次调用 · "
                            f"{sum(h.get('rows', 0) for h in hits)} 行",
                "reason": "规划未覆盖，执行中补充",
            })
        return rows

    # ---------------------------------------------------------------- 假设回应
    def _declared_param_keys(self) -> set[str]:
        """从本体声明动态收集参数名/字段名，代码里不维护「知识词→参数名」表。"""
        raw = self.sem.raw or {}
        keys: set[str] = set()
        for spec in (raw.get("pricing") or {}).values():
            if isinstance(spec, dict):
                for row in spec.get("matrix") or []:
                    if isinstance(row, dict):
                        keys.update(str(k) for k in (row.get("params") or {}))
        for section in ("rules", "actions", "state_machines"):
            data = raw.get(section) or []
            if isinstance(data, dict):
                data = list(data.values())
            for item in (data or [])[:200]:
                if isinstance(item, dict):
                    keys.update(str(k) for k in item)
        return {k for k in keys if len(k) >= 4}

    def _declaration_hit(self, claim: str) -> str | None:
        """声明命中：用问句/假设里的本体术语（类型名、费用显示名、声明
        标签、参数名）与已查到的 TBox 声明求交集；不用中文词表。"""
        from freight.answerability import _match, _terms_of

        text = f"{claim} {self.question}"
        needles: set[str] = {
            t for t in _terms_of(text) if len(t) >= 3 and not t.isascii()
        }
        needles.update(k for k in self._declared_param_keys() if k in text)
        try:
            hits = _match(self.system, text)
            for tid in (hits.get("types") or {}):
                cls = self.sem.classes.get(tid) or {}
                for form in (cls.get("label"), tid):
                    if isinstance(form, str) and len(form) >= 3:
                        needles.add(form)
            for label in (hits.get("fee_index") or {}):
                if len(str(label)) >= 3:
                    needles.add(str(label))
        except Exception:  # noqa: BLE001
            pass
        if not needles:
            return None
        for item in self.tbox_seen.values():
            if item.get("kind") not in ("rule", "pricing", "action"):
                continue
            haystack = " ".join(
                str(item.get(k) or "") for k in ("id", "label", "detail")
            )
            if any(n in haystack for n in needles):
                return (f"{item['kind']}|{item['id']}："
                        + str(item.get("detail") or "")[:140])
        return None

    @staticmethod
    def _check_from_verify_by(verify_by: str) -> str | None:
        """从假设自己声明的验证手段（工具名/字段名）推出确定性检查 ID；
        推不出就返回 None，让结论如实标「待补证」。"""
        text = (verify_by or "").lower()
        for token, check in (
            ("fee_breakdown", "rule_delta"),
            ("rule_delta", "rule_delta"),
            ("quote_reconcile", "quote_drift"),
            ("snapshot", "quote_drift"),
            ("evidence", "evidence_complete"),
            ("checksum", "evidence_complete"),
            ("pricing_rule_expired", "rule_expired"),
            ("生效期", "rule_expired"),
            ("tbox", "declaration_hit"),
            ("describe", "declaration_hit"),
        ):
            if token in text:
                return check
        return None

    def _hypothesis_verdicts(self) -> list[dict]:
        """逐条假设给 成立/排除/待补证，证据全部来自确定性分析结果。

        判定绑定假设自己声明的确定性检查（check / verify_by 里的工具名），
        不靠 claim 的措辞关键词；没有绑定检查就如实标「待补证」。
        """
        analysis = self.system.fee_analysis(self.object_id) or {}
        findings = analysis.get("findings") or []
        codes = {f.get("code") for f in findings}
        breakdown = analysis.get("breakdown") or []
        evidence = analysis.get("evidence") or []
        out = []
        for hypo in self.planned.get("hypotheses", []):
            claim = hypo["claim"]
            check = hypo.get("check") or self._check_from_verify_by(
                hypo.get("verify_by") or "",
            )
            verdict, why = "待补证", "假设未绑定可执行的确定性检查，无法自动裁定"
            decl = self._declaration_hit(claim)
            if decl:
                verdict, why = "成立", decl
            elif check == "rule_delta":
                if "WAITING_FEE_MISMATCH" in codes or any(
                        abs(b.get("rule_delta") or 0) > 0.01 for b in breakdown):
                    bad = [b for b in breakdown
                           if abs(b.get("rule_delta") or 0) > 0.01]
                    verdict = "成立"
                    why = ("；".join(
                        f"{b['label']} 应计 {b['rule_expected']:.2f} / "
                        f"实收 {b['actual_amount']:.2f}" for b in bad[:3]))
                elif breakdown:
                    verdict = "排除"
                    why = "全部费用项 rule_delta ≈ 0，应计与实收一致"
            elif check == "quote_drift":
                if "QUOTE_SNAPSHOT_DRIFT" in codes:
                    verdict = "成立"
                    why = "报价快照与明细不一致（QUOTE_SNAPSHOT_DRIFT）"
                elif analysis:
                    verdict = "排除"
                    why = "三口径互校一致"
            elif check == "evidence_complete":
                missing = [f for f in findings
                           if str(f.get("code", "")).startswith("GAP")]
                if missing:
                    verdict = "成立"
                    why = "；".join(
                        f"{f['code']}({f.get('fee_id')})" for f in missing[:3])
                elif evidence:
                    verdict = "排除"
                    why = f"{len(evidence)} 条证据均在位且带 checksum"
            elif check == "rule_expired":
                if "PRICING_RULE_EXPIRED" in codes:
                    verdict = "成立"
                    why = "费用发生时间超出规则生效期"
                else:
                    verdict = "排除"
                    why = "费用发生时间在规则生效期内"
            out.append({"id": hypo["id"], "claim": claim,
                        "verdict": verdict, "evidence": why,
                        "verify_by": hypo["verify_by"],
                        "check": check or "unbound"})
        return out

    # ---------------------------------------------------------------- 角色出口
    ROLE_LABELS = {
        "finance": "财务", "supervisor": "主管", "cs": "客服",
        "operator": "运营", "driver": "司机", "shipper": "货主",
        "admin": "管理员",
    }

    def _role_exports(self) -> list[dict]:
        """按角色的下一步：谁、做什么、对谁做、为什么。

        来源是系统动作提案（execution_orders/action_plan 的确定性产物），
        不是模型编的——角色出口因此可执行、可审计。
        """
        plan = self.system.action_plan(self.object_id) or {}
        rows = []
        for action in plan.get("actions") or []:
            if not action.get("allowed"):
                continue
            roles = action.get("roles") or [action.get("role") or ""]
            rows.append({
                "role": "、".join(
                    self.ROLE_LABELS.get(str(r), str(r)) for r in roles if r),
                "action": action.get("label") or action.get("action_id"),
                "target": action.get("target") or "—",
                "why": f"目标状态 {action.get('target_status') or '—'}；"
                       f"金额 {json.dumps(action.get('payload') or {}, ensure_ascii=False)}",
            })
        for pre in plan.get("prerequisites") or []:
            rows.append({
                "role": "客服",
                "action": pre.get("label") or pre.get("action_id"),
                "target": pre.get("target") or "—",
                "why": pre.get("status") or "前置条件",
            })
        return rows

    def _open_items(self) -> list[dict]:
        """未决项：缺什么、建议找谁、哪个系统、阻塞哪条结论。"""
        items = []
        for row in self._plan_execution_review():
            if row["status"] == "未执行":
                items.append({
                    "missing": f"未完成任务「{row['intent']}」（{row['tool']}）",
                    "owner": "诊断执行方",
                    "system": row["tool"],
                    "blocks": row["intent"],
                })
        analysis = self.system.fee_analysis(self.object_id) or {}
        for finding in analysis.get("findings") or []:
            if str(finding.get("code", "")).startswith("GAP"):
                items.append({
                    "missing": f"{finding.get('code')}：{finding.get('message', '')[:60]}",
                    "owner": "数据归属方",
                    "system": finding.get("source_system") or "业务系统",
                    "blocks": "该费用的最终口径确认",
                })
        return items

    def _step_exhausted(self) -> None:
        hypo_lines = "\n".join(
            f"{h['id']} {h['claim']}" for h in self.planned.get("hypotheses", [])
        )
        if self.question_kind == "knowledge":
            evidence_rule = (
                "\n这是知识型问题：最终答案必须引用对话中已出现的 TBox 声明——"
                "规则 ID、公式、定价参数值（如免费分钟数、每块单价）、"
                "状态机迁移。凡声明已在上下文中的假设，必须给出参数值并判"
                "「成立」，禁止标待补证；只有上下文确实没有的声明才允许待补证。"
            )
        else:
            evidence_rule = (
                "\n已核对的 TBox 声明与 ABox 观察同样是证据：规则公式、定价参数、"
                "状态机迁移可直接引用其 ID 与参数值作答，不要把已有声明标成待补证。"
            )
        instruction = (
            "工具调用次数已用完。请基于以上观察，只输出 final JSON："
            '{"action":"final","title":"结论","question":"最终核对的问题",'
            '"thought":"综合判断","answer":"最终结论"}\n'
            "answer 必须包含三段：① 逐条回应以下假设（成立/排除/待补证+证据）：\n"
            + (hypo_lines or "（无假设）")
            + "\n② 已核对的事实；③ 未决项（缺什么、建议找谁、哪个系统）。"
            + evidence_rule
        )
        self.messages.append({"role": "user", "content": instruction})
        started = time.perf_counter()
        try:
            raw = self.client.chat(self.messages, thinking=False)
        except LLMError as exc:
            self.error = str(exc)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        candidate = self._parse_action(raw)
        if not candidate or candidate.get("action") != "final":
            # 降级而不是报错：工具预算用尽时，模型没给出合法 final JSON 是常见情况。
            # 演示（以及线上排查）更需要"一个基于已有观察的初步结论"，
            # 而不是整条链 503 白屏。降级结论会明确标注"初步"与观察条数。
            candidate = self._degraded_final(raw)
            self.degraded = True
        self.final_action = candidate
        self.messages.append({"role": "assistant", "content": raw[:4000]})
        self.pending = "action"
        self.steps.append(self._llm_step(
            index=len(self.steps) + 1,
            title="工具次数用完，大模型归纳观察",
            tag="LLM · 归纳",
            messages=self.messages,
            output=raw,
            parsed=candidate,
            thought=candidate.get("thought") or "",
            ok=True,
            error=None,
            elapsed_ms=elapsed_ms,
            conclusion="已基于全部观察归纳结论。",
            next_text="生成动作提案（不调用大模型）",
            thinking=False,
        ))

    def _step_action(self) -> None:
        """一次点击只生成本地动作提案，不调用大模型，也不追加最终结论。"""
        if self.question_kind == "knowledge":
            # 知识型问题没有对象状态变化诉求：不出动作提案，直接收口。
            # （提案本身也不写数据，跳过只为避免答非所问的建议。）
            self.pending = "finish"
            return
        self.plan = self.system.action_plan(self.object_id) or {}
        self.steps.append(self._action_step(self.plan, len(self.steps) + 1))
        self.pending = "finish"

    def _step_finish(self) -> None:
        self._finish()

    def _next_label(self) -> str:
        if self.finished:
            return "已完成"
        if self.pending == "catalog":
            return "紧凑 TBox 目录装载（全量优先，不调用大模型）"
        if self.pending == "plan":
            return "大模型规划（调用 LLM 选择本体范围）"
        if self.pending == "tbox":
            return "本体定义查询 tbox.describe（不调用大模型）"
        if self.pending == "tbox_query":
            return "本体定义查询 tbox.describe（按需，不调用大模型）"
        if self.pending == "tbox_search":
            return "本体 schema 检索 ontology.schema（本地召回，不调用大模型）"
        if self.pending == "object_type_search":
            return "对象类型锚点检索 ontology.object_type_search（不调用大模型）"
        if self.pending == "decide":
            if len(self.observations) + self.tbox_queries >= self.max_steps:
                return "大模型归纳（调用 LLM，检索次数已用完）"
            return "大模型决策（调用 LLM 决定下一步）"
        if self.pending == "tool":
            tool = (self.pending_action or {}).get("tool") or "业务事实"
            return f"业务事实查询（ABox · {tool}，不调用大模型）"
        if self.pending == "review":
            return "大模型复核（调用 LLM）"
        if self.pending == "action":
            return "生成本地动作提案（不调用大模型，不写业务数据）"
        if self.pending == "finish":
            return "整理大模型 final 结论（不新增替代结论）"
        return "执行下一步"

    def _finish(self) -> None:
        if self.finished:
            return
        if self.final_action is None:
            raise LLMError("Agent 没有拿到大模型 final 结论，不能生成本地替代结论")
        self.finished = True
        self.pending = "done"
        self.steps.append(self._final_step(
            self.final_action, self.observations, len(self.steps) + 1,
            self.error, self.plan,
        ))
        # C5 查询历史回写：本次问句实际生效的类型（规划选定 ∪ 目录锚点），
        # 落 runs/ 不入库；供后续同类问句的 history 通道召回。
        self._record_history()

    def _record_history(self) -> None:
        """推理结束回写「问句 → 实际使用类型」（失败不阻塞主链路）。"""
        try:
            planned = [str(t) for t in ((self.plan or {}).get("types") or [])
                       if str(t) in self.sem.classes]
            effective = list(dict.fromkeys(planned + self.catalog_anchor_ids))
            if not self.question_is_default and effective:
                self.sem.record_retrieval_history(self.question, effective)
        except Exception:  # noqa: BLE001 历史回写是增强，不是承重
            pass

    @staticmethod
    def _clip(text: str, limit: int = 90) -> str:
        text = " ".join(str(text or "").split())
        if len(text) <= limit:
            return text
        return text[:limit - 1] + "…"

    def _summary_for_step(self, step: dict) -> dict:
        """给总览表用的一句话：谁、做了什么、产出什么。"""
        query = step.get("query") or {}
        title = str(step.get("title") or "")
        tag = str(step.get("tag") or "")
        api = str(query.get("api") or "")
        conclusion = str(step.get("conclusion") or "")
        if "proposal" in step:
            who, action, mode = "Agent → 人工", "生成动作提案", "人工确认"
            output = conclusion or "只建议，不写入业务数据"
        elif "scope" in query:
            resolved = query.get("resolved") or {}
            who = "本体引擎（TBox）"
            action = "装载紧凑目录并裁剪可用能力"
            mode = "确定性"
            output = (
                f'{resolved.get("mode", "目录")} · '
                f'{resolved.get("types", 0)} 个类型 / '
                f'{resolved.get("relations", 0)} 条关系 / '
                f'{len(self.catalog)} 个能力'
            )
        elif "llm" in step:
            who = "大模型"
            action = title.replace("（调用 LLM）", "").strip() or "大模型决策"
            mode = "LLM"
            output = conclusion or "已返回可解析结果"
        elif "mcp/call" in api:
            tool = str(query.get("expression") or "").split("(", 1)[0]
            who = "业务系统（ABox）"
            action = f"查询 {tool}".strip()
            mode = "确定性"
            output = conclusion or "已返回业务事实"
        elif tag == "Agent · Final" or query.get("expression") == "final()":
            who = "大模型 + 业务数据"
            action = "综合判断形成结论"
            mode = "LLM + 规则"
            output = conclusion or "已形成结论"
        else:
            who = "本体引擎（TBox）"
            action = title or "查询本体定义"
            mode = "确定性"
            output = conclusion or "已完成"
        return {
            "who": who,
            "action": action,
            "output": self._clip(output),
            "mode": mode,
        }

    def _next_actor(self) -> str:
        if self.finished:
            return "已完成"
        return {
            "catalog": "本体引擎（TBox）",
            "plan": "大模型",
            "tbox": "本体引擎（TBox）",
            "tbox_query": "本体引擎（TBox）",
            "tbox_search": "本体引擎（TBox）",
            "object_type_search": "本体引擎（TBox）",
            "tbox_path": "本体引擎（TBox）",
            "decide": "大模型",
            "tool": "业务系统（ABox）",
            "review": "大模型",
            "action": "人工确认",
            "finish": "大模型 + 业务数据",
        }.get(self.pending, "Agent")

    # 链路固定阶段骨架：start 那一刻总览就是完整六段，执行只更新状态、
    # 计数与明细——阶段文案固定不动，总览是执行台账的目录页，不是售前示意图。
    # 顺序按业务叙事（结论之后落到"谁去做什么"）；各阶段真实步骤序号见 details。
    _PLANNED_STAGES = [
        ("catalog", "紧凑 TBox 目录装载", "本体引擎（TBox）",
         "按强依赖白名单展开对象能力集（全量优先，不调用大模型）",
         "工具白名单 + 可圈定类型",
         "代码固定推进 → 目录与工具白名单装进规划提示词，大模型只能从中挑"),
        ("plan", "链路规划", "大模型",
         "把业务问题拆成取数计划：类型/口径/事实 + 取证重点裁剪与假设清单",
         "行动计划（任务清单 + 待验证假设）",
         "代码固定推进 → types/fee_types 作为 describe() 入参，圈定要查的本体声明"),
        ("tbox", "本体声明查询", "本体引擎（TBox）",
         "命中类定义、计价规则、关系与定价参数（取证中可按需回查）",
         "命中的本体声明",
         "代码固定推进 → 命中的规则公式与取证任务拼进决策提示词，进入取证循环"),
        ("facts", "业务事实取证", "大模型决策 + 业务系统（ABox）",
         "多轮「大模型决策 → 工具调用」循环取证，直到信息足够或预算用尽",
         "事实数据（每次调用带行数与证据）",
         "大模型决定收口 → 返回 final 动作（或预算用尽被迫归纳），全部观察随对话历史交给结论汇总"),
        ("final", "结论汇总", "大模型 + 规则",
         "复核并归纳结论：假设逐条裁定，附证据、版本与 checksum",
         "诊断结论 + 收口三件套",
         "代码固定推进 → 结论与假设裁定交给本地提案器，按角色生成动作提案（不调大模型）"),
        ("action", "动作出口", "按角色 · Agent",
         "提案谁补证、谁退费、找哪个系统；动作等人工确认后才执行",
         "动作提案（含未决项清单）",
         "等人工确认 → 确认前不写任何业务数据；链路到此为止"),
    ]

    @classmethod
    def _planned_stage_of(cls, step: dict) -> str:
        """把实际步骤归并到计划阶段（tag 前缀映射）。

        归并原则与阶段叙事一致：取证循环里的大模型决策属于「业务事实取证」
        （它决定取什么数）；复核/预算穷尽归纳/最终结论/降级属于「结论汇总」；
        动作提案属于「动作出口」——不再把所有 LLM 步骤堆进「链路规划」。
        """
        tag = str(step.get("tag") or "")
        if tag == "TBox · 紧凑目录":
            return "catalog"
        if tag == "LLM · 本体规划":
            return "plan"
        if tag.startswith("TBox"):
            return "tbox"
        if tag == "Agent · Final":
            return "final"
        if tag == "LLM · 决策" or tag.startswith("Agent"):
            return "facts"
        if tag in ("LLM · 复核", "LLM · 归纳", "LLM · 失败降级"):
            return "final"
        if tag.startswith("动作"):
            return "action"
        return "tool"

    def _stage_output(self, sid: str, group: dict) -> str:
        """各阶段产出摘要：确定性生成，不用模型措辞。

        阶段还没有步骤时统一显示骨架产出——start 视图不该出现
        "0 次调用"或提前暴露动作提案计数（那会像已经诊断完）。
        """
        counts = group["counts"]
        if sid == "plan" and counts["steps"]:
            focus = self.planned.get("focus_fee_types") or []
            fees = self.planned.get("fee_types") or []
            hyp = self.planned.get("hypotheses") or []
            tasks = self.planned.get("tasks") or []
            rate = round((1 - len(focus) / len(fees)) * 100) if fees else 0
            return (f"取证重点 {len(focus)}/{len(fees)} 种（裁剪率 {rate}%）"
                    f"· 假设 {len(hyp)} 条 · 任务 {len(tasks)} 条")
        if sid == "plan" and self.finished:
            return "规划未完成（降级）"
        if sid == "tbox" and counts["steps"]:
            return f"{counts['steps']} 次本体查询 · {counts['rows']} 行声明"
        if sid == "facts" and counts["steps"]:
            parts = [f"{counts['tool']} 次工具调用 · {counts['llm']} 次大模型决策"]
            if counts["rows"]:
                parts.append(f"取回 {counts['rows']} 行")
            return " · ".join(parts)
        if sid == "final" and counts["steps"]:
            verdicts = self._hypothesis_verdicts()
            tally: dict[str, int] = {}
            for row in verdicts:
                tally[row["verdict"]] = tally.get(row["verdict"], 0) + 1
            text = "假设裁定：" + " · ".join(
                f"{k} {v}" for k, v in sorted(tally.items())) if tally else "观察归纳"
            if group.get("degraded"):
                text = "降级收口 · " + text
            return text
        if sid == "action" and counts["steps"]:
            roles = self._role_exports()
            items = self._open_items()
            return (f"{len(roles)} 条角色提案 · 未决项 {len(items)} 条"
                    " · 等人工确认")
        # 其余（catalog / 未开始阶段）：骨架产出
        for row in self._PLANNED_STAGES:
            psid, output = row[0], row[4]
            if psid == sid:
                return output
        return "—"

    def _stage_summaries(self) -> list[dict]:
        """总览 = 六段固定骨架 + 执行轨迹聚合（可审计台账）。

        设计约束（与"一眼看完"的承诺一致）：
        - 阶段文案固定来自骨架，执行只更新状态/计数/明细——不会跑到链尾
          把「链路规划」改写成别的内容；
        - 状态语义真实：出现过步骤即完成；链结束后没到的阶段标「未到达」，
          降级/预算穷尽落在「结论汇总」并显式标注；动作出口恒为
          「等人工确认」（动作从不自动执行）；
        - 每阶段聚合真实步骤数/工具调用数/大模型调用数，明细逐步可回看。
        """
        groups: dict[str, dict] = {}
        order: list[str] = []
        for sid, name, who, action, output, handoff in self._PLANNED_STAGES:
            groups[sid] = {"id": sid, "name": name, "who": who,
                           "action": action, "output": output,
                           "handoff": handoff,
                           "status": "pending", "details": [],
                           "counts": {"steps": 0, "llm": 0, "tool": 0, "rows": 0},
                           "degraded": False}
            order.append(sid)

        for step in self.steps:
            key = self._planned_stage_of(step)
            group = groups.get(key)
            if group is None:  # 计划外（兜底）：按实际追加，不丢信息
                tag = str(step.get("tag") or "step")
                group = {"id": key, "name": tag,
                         "who": str(step.get("source") or "Agent"),
                         "action": step.get("title") or tag, "output": "",
                         "handoff": "",
                         "status": "done", "details": [],
                         "counts": {"steps": 0, "llm": 0, "tool": 0, "rows": 0},
                         "degraded": False}
                groups[key] = group
                order.append(key)
            tag = str(step.get("tag") or "")
            counts = group["counts"]
            counts["steps"] += 1
            if tag.startswith("LLM"):
                counts["llm"] += 1
            if tag.startswith("Agent") and step.get("tool"):
                counts["tool"] += 1
            # TBox 步骤顶层没有业务行数：声明条数在 datasets[0].rows
            # （工具步骤顶层 rows 才是业务行数，两者语义不同）。
            if tag.startswith("TBox"):
                datasets = ((step.get("data") or {}).get("datasets") or [{}])
                counts["rows"] += len(datasets[0].get("rows") or [])
            else:
                counts["rows"] += int(step.get("rows") or 0)
            if tag in ("LLM · 失败降级", "LLM · 归纳"):
                group["degraded"] = True
            detail = {
                "no": len(group["details"]) + 1,
                "index": step.get("index"),
                "title": step.get("title") or tag,
                "tag": tag,
                "ok": step.get("ok") is not False,
            }
            if key == "facts" and step.get("tool"):
                # 工具明细保留可审计字段：系统/工具/参数/行数
                layer = step.get("layers") or {}
                api = str((step.get("query") or {}).get("api") or "")
                system = api.split("·", 1)[1].strip() if "·" in api else ""
                detail.update({
                    "system": system,
                    "tool": step.get("tool") or "—",
                    "question": layer.get("question") or step.get("title") or "",
                    "arguments": step.get("arguments") or {},
                    "rows": int(step.get("rows") or 0),
                })
            group["details"].append(detail)

        # 规划 LLM 失败时 planned 可能没建出来：final 摘要复用收口兜底
        if not self.planned.get("hypotheses"):
            self.planned["hypotheses"] = self._normalize_hypotheses(None)

        pending_stage = {
            "catalog": "catalog", "plan": "plan",
            "tbox": "tbox", "tbox_query": "tbox", "tbox_search": "tbox",
            "object_type_search": "tbox", "tbox_path": "tbox",
            "decide": "facts", "tool": "facts",
            "review": "final", "finish": "final", "action": "action",
        }.get(self.pending or "")
        for sid in order:
            group = groups[sid]
            if sid == "action" and group["counts"]["steps"]:
                group["status"] = "proposed"  # 动作从不自动执行
            elif group["counts"]["steps"]:
                group["status"] = "degraded" if group["degraded"] else "done"
            elif self.finished:
                group["status"] = "skipped"   # 链已结束却没到：显式暴露
            elif sid == pending_stage:
                group["status"] = "current"

        return [{
            "id": groups[key]["id"],
            "name": groups[key]["name"],
            "who": groups[key]["who"],
            "action": groups[key]["action"],
            "output": self._clip(self._stage_output(key, groups[key])
                                 or "—", 90),
            "handoff": groups[key]["handoff"],
            "status": groups[key]["status"],
            "counts": groups[key]["counts"],
            "degraded": groups[key]["degraded"],
            "details": groups[key]["details"],
        } for key in order]

    def result(self) -> dict:
        tools_used = sorted({
            obs["tool"] for obs in self.observations if obs.get("tool")
        })
        steps = []
        for step in self.steps:
            item = dict(step)
            item["summary"] = self._summary_for_step(step)
            steps.append(item)
        stages = self._stage_summaries()
        # 参与方按真实步骤聚合：who 是 _summary_for_step 的派生字段
        # （步骤本体没有 who），工具步骤带系统后缀的按主干归并——
        # "N 参与方"不再是写死文案去重的结果。
        participants: list[str] = []
        for item in steps:
            who = str((item.get("summary") or {}).get("who") or "").strip()
            if "·" in who:
                who = who.split("·", 1)[0].strip()
            if who and who not in participants:
                participants.append(who)
        if not participants:
            for stage in stages:
                if stage["who"] not in participants:
                    participants.append(stage["who"])
        return {
            "title": "运单费用诊断 · Agent 推理链",
            "question": self.question,
            "waybill_id": self.object_id,
            "generated_at": now_iso(),
            "mode": "agent",
            "model": self.model_label,
            "finished": self.finished,
            "pending": None if self.finished else self.pending,
            "next_label": None if self.finished else self._next_label(),
            "max_steps": self.max_steps,
            "summary": {
                "executed_steps": len(steps),
                "query_budget": self.max_steps,
                "total_stages": len(stages),
                "finished": self.finished,
                "participants": participants,
                "next_actor": self._next_actor(),
                "stages": stages,
            },
            "agent": {
                "ok": self.error is None,
                "degraded": bool(self.degraded),
                "running": not self.finished,
                "model": self.model_label,
                "endpoint": self.endpoint_label,
                "tool_calls": len(self.observations),
                "tools_used": tools_used,
                "steps": len(self.steps),
                "elapsed_ms": round(
                    (time.perf_counter() - self.started_at) * 1000
                ),
                "error": self.error,
            },
            "steps": steps,
        }

    def _llm_step(self, index: int, title: str, tag: str,
                  messages: list[dict], output: str,
                  parsed: dict | None, ok: bool, error: str | None,
                  elapsed_ms: int, conclusion: str, next_text: str,
                  thought: str = "", thinking: bool | None = None) -> dict:
        """展示一次大模型调用的完整输入与原始输出，供用户逐步确认。"""
        system = ""
        turns = []
        for message in messages:
            role = str(message.get("role") or "")
            content = str(message.get("content") or "")
            if role == "system":
                system = content
            else:
                turns.append({"role": role, "content": content})
        thinking = self.client.step_thinking if thinking is None else thinking
        params = {
            "model": self.client.model,
            "temperature": 0.1,
            "max_tokens": self.client.max_tokens,
            "enable_thinking": thinking,
            "stream": True,
        }
        return {
            "index": index,
            "kind": "llm",
            "title": title,
            "tag": tag,
            "actor": "agent",
            "query": {
                "label": "大模型调用 · 输入",
                "expression": f"POST {self.client.base}/chat/completions",
                "api": "OpenAI 兼容网关 · SSE 流式",
                "params": params,
            },
            "llm": {
                "system": system,
                "messages": turns,
                "output": output,
                "parsed": parsed,
                "error": error,
                "thinking": thinking,
            },
            "operations": [
                f"装载 system prompt（{len(system)} 字）",
                f"提交 {len(turns)} 条对话消息",
                "逐个读取流式分片，拼接完整输出后解析 JSON",
            ],
            "data": {"datasets": []},
            "facts": [
                {"label": "模型", "value": self.client.model},
                {"label": "消息数", "value": str(len(turns))},
                {"label": "输出字符", "value": str(len(output or ""))},
                {"label": "耗时", "value": f"{elapsed_ms} ms"},
                {"label": "思考模式", "value": "开" if thinking else "关"},
            ],
            "checks": [{
                "pass": bool(ok),
                "label": "调用与解析",
                "expected": "返回可解析结果",
                "actual": "成功" if ok else (error or "失败"),
            }],
            "judgment": (
                thought
                or (parsed or {}).get("thought")
                or (parsed or {}).get("reason")
                or ("调用成功" if ok else (error or "调用失败"))
            ),
            "conclusion": conclusion,
            "next": next_text,
            "graph": {"root": "", "nodes": [], "edges": []},
            "source": (
                f"大模型网关 {self.client.base}；"
                f"输入 {len(turns)} 条消息，输出 {len(output or '')} 字符。"
            ),
            "basis": f"模型 {self.client.model} · 本次调用的输入与输出均在本步展示",
            "ok": ok,
            "elapsed_ms": elapsed_ms,
        }

    # ------------------------------------------------------------------ 问答前置 TBox
    @staticmethod
    def _parse_plan(raw: str) -> dict | None:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        start = text.find("{")
        if start < 0:
            return None
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None

    def _plan_tbox(self) -> dict:
        """只把 schema search + 闭包结果喂给模型，让它在邻域内圈定范围。

        规划产出三段（都可被确定性兜底，模型不给也能跑）：
        - types / fee_types：声明范围（tbox.describe 用，可以宽）
        - focus_fee_types：**取证重点**，必须窄——列全部等于没规划，
          步骤里用「圈定 N / 目录 M」的裁剪率度量规划质量
        - tasks：取证任务清单（意图 / 建议工具 / 目标 / 理由），
          执行阶段逐条对照，未执行的要给理由
        - hypotheses：可证伪假设（claim + verify_by），结论必须逐条回应
        """
        catalog = self.catalog_index
        class_items = [item for item in catalog if item["kind"] == "class"]
        price_items = [item for item in catalog if item["kind"] == "pricing"]
        classes = "；".join(
            f'{item["id"]}|{item["label"]}' for item in class_items
        )
        fee_types = "、".join(
            item["id"].split(".", 1)[1] for item in price_items
        ) or "无"
        all_fee_ids = [
            item["id"].split(".", 1)[1] for item in price_items
        ] or list(self.sem.pricing.keys())
        if not classes:
            classes = "；".join(
                f'{cls["id"]}|{cls.get("label", cls["id"])}'
                for cls in self.sem.classes.values()
            )
            fee_types = "、".join(self.sem.pricing.keys()) or "无"
        if len(classes) > CATALOG_PROMPT_LIMIT:
            total = len(class_items) or len(self.sem.classes)
            classes = (
                classes[:CATALOG_PROMPT_LIMIT]
                + f"…（已截断，召回共 {total} 个类型）"
            )
        if len(fee_types) > CATALOG_PROMPT_LIMIT:
            fee_types = fee_types[:CATALOG_PROMPT_LIMIT] + "…（已截断）"
        if self.catalog_mode == "full":
            catalog_note = "全量紧凑目录（在上下文预算内）" if self.catalog_fits \
                else "全量紧凑目录（超出预算）"
        else:
            catalog_note = (
                "按聚合缩域后的紧凑目录（聚合 "
                + ("、".join(self.catalog_aggregate_ids) or "—")
                + "；跨聚合边界链接已注入）"
            )
        tools_hint = "、".join(sorted(self.catalog.keys())) or "无"
        if self.question_kind == "knowledge":
            kind_rule = (
                "\n本次是**知识型问题**：用户要的是本体声明（规则/定价/状态/"
                "动作），不是实例取证。tasks 只需要声明读取任务（tool_hint 用 "
                "tbox.describe / ontology.state_machine / ontology.schema，"
                "不要给 function.* 或实例查询）；hypotheses 围绕声明的具体"
                "内容（如免费时长是多少分钟、谁能执行哪个动作）。"
            )
        elif self.question_kind == "mixed":
            kind_rule = (
                "\n本次是**混合型问题**：规则与口径部分用 tbox.describe 读声明，"
                "金额与证据部分照常规划实例取证。"
            )
        else:
            kind_rule = ""
        messages = [{
            "role": "system",
            "content": (
                "你是本体范围规划器。系统已按 Palantir 路径先给紧凑 TBox 目录："
                "全量装得下就直接给全量，装不下才按聚合缩域——当前对象所在聚合"
                "必选（业务约束），其余聚合由问句在全部上下文里检索召回的锚点"
                "决定；跨聚合关系作为边界链接注入（端点带聚合名，不会静默"
                "丢失）。不预计算全局强依赖闭包。你只能从下面目录候选里选择"
                "后续要查定义和事实的最小充分类型集合；目录只是索引，字段、"
                "规则和关系端点在 tbox.describe 或 ontology.search_around 中"
                "再按需读取。目录按「上下文 → 聚合 → 类型」组织：同聚合类型"
                "是同一业务对象的整体，边界链接表示跨业务对象的路径，规划时"
                "可以沿它进入相邻聚合的类型。"
                "不要用问题关键词做意图分类，只按目录元数据和工具契约选择。"
                "不要输出任何目录之外的本体 ID。只输出一个 JSON 对象，"
                "不要 Markdown，不要解释。\n"
                "规划质量硬约束：① focus_fee_types 是**取证重点**，只列与本问题"
                "直接相关的费用类型（通常 1-3 个），把全部费用类型列进 focus 等于"
                "没规划；② tasks 是取证任务清单，每条给 intent / tool_hint / "
                "target / why，tool_hint 必须来自可用能力集；③ hypotheses 是"
                "可证伪假设，每条给 id / claim / verify_by，结论阶段必须逐条回应。"
                + kind_rule
            ),
        }, {
            "role": "user",
            "content": (
                f"业务问题：{self.question}\n"
                f"目录状态：{catalog_note}\n"
                f"{self.catalog_context}\n"
                f"目录中的类型：{classes}\n"
                f"目录中的费用类型：{fee_types}\n"
                f"可用能力（工具）：{tools_hint}\n"
                '输出格式：{"types":["<目录中的类型 ID>"],'
                '"fee_types":["<目录中的费用类型 ID>"],'
                '"focus_fee_types":["<取证重点费用类型，通常 1-3 个>"],'
                '"focus_reason":"为什么是这几个",'
                '"tasks":[{"intent":"要验证什么","tool_hint":"<可用能力中的工具>",'
                '"target":"查什么对象","why":"为什么必须查"],'
                '"hypotheses":[{"id":"H1","claim":"可证伪的判断",'
                '"verify_by":"用什么证据验证"}],'
                '"reason":"选择理由"}'
            ),
        }]
        started = time.perf_counter()
        try:
            raw = self.client.chat(messages, thinking=False)
        except LLMError as exc:
            self.error = str(exc)
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        planned = self._parse_plan(raw)
        if not planned:
            raise LLMError("本体规划输出无法解析，未进入后续查询")
        raw_types = planned.get("types") if isinstance(planned.get("types"), list) else []
        raw_fees = (
            planned.get("fee_types")
            if isinstance(planned.get("fee_types"), list) else []
        )
        types = [str(t) for t in raw_types if str(t) in self.sem.classes]
        fees = [str(f) for f in raw_fees if str(f) in self.sem.pricing]
        if not types:
            raise LLMError("本体规划输出没有合法类型，未进入后续查询")
        # focus：模型没给 / 不合法时，按问句在本体里的命中确定焦点；
        # 不用「英文 fee_type 是否为中文问句子串」这种恒不命中的判断。
        raw_focus = planned.get("focus_fee_types")
        focus = [str(f) for f in (raw_focus if isinstance(raw_focus, list) else [])
                 if str(f) in self.sem.pricing]
        self.focus_source = "model" if focus else ""
        if not focus:
            focus = self._focus_from_question(fees)
        tasks = self._normalize_tasks(planned.get("tasks"), focus)
        hypotheses = self._normalize_hypotheses(planned.get("hypotheses"))
        return {
            "types": types,
            "fee_types": fees,
            "focus_fee_types": focus,
            "focus_source": self.focus_source,
            "focus_reason": str(planned.get("focus_reason") or ""),
            "tasks": tasks,
            "hypotheses": hypotheses,
            "reason": str(planned.get("reason") or "大模型圈定类型范围"),
            "_messages": messages,
            "_output": raw,
            "_error": None,
            "_elapsed_ms": elapsed_ms,
        }

    def _focus_from_question(self, fees: list[str]) -> list[str]:
        """按问句命中的费用显示名定焦点；没命中则按模型声明顺序取，
        并显式标注来源，不冒充「按问题圈定」。"""
        try:
            from freight.answerability import _match
            hits = _match(self.system, self.question)
            matched = [f for f in (hits.get("fee_types") or {}) if f in fees]
            if matched:
                self.focus_source = "question_ontology_match"
                return matched
        except Exception:  # noqa: BLE001
            pass
        self.focus_source = "model_order_fallback"
        return fees[:1]

    def _normalize_tasks(self, raw, focus: list[str]) -> list[dict]:
        """取证任务清单：模型给的就地校验，缺了按本次问句与焦点补齐。

        校验三条：tool_hint 在可用能力集内、intent 非空、target 非空。
        兜底任务显式标注「非模型规划」，不把固定剧本冒充规划结果。
        """
        tasks: list[dict] = []
        seen: set[str] = set()
        for item in (raw if isinstance(raw, list) else []):
            if not isinstance(item, dict):
                continue
            intent = str(item.get("intent") or "").strip()
            tool = str(item.get("tool_hint") or "").strip()
            if not intent or tool not in self.catalog or tool in seen:
                continue
            seen.add(tool)
            tasks.append({
                "intent": intent,
                "tool_hint": tool,
                "target": str(item.get("target") or self.object_id),
                "why": str(item.get("why") or "规划器圈定的取证任务"),
            })
        if tasks:
            return tasks
        labels = [
            str((self.sem.pricing.get(f) or {}).get("label") or f)
            for f in focus
        ]
        focus_label = "、".join(labels) or "当前对象"
        for tool in ("function.fee_breakdown", "function.quote_reconcile",
                     "function.evidence_chain"):
            if tool in self.catalog:
                tasks.append({
                    "intent": f"核对{focus_label}｜{self.question[:40]}",
                    "tool_hint": tool,
                    "target": self.object_id,
                    "why": "规划器未输出任务；按焦点与可用能力集生成的兜底取证（非模型规划）",
                })
        return tasks

    def _normalize_hypotheses(self, raw) -> list[dict]:
        """可证伪假设清单：模型给的就地校验；缺了按本次问句命中的本体
        要素生成检查项，每条绑定确定性检查，不写死具体差异结论。"""
        items: list[dict] = []
        for index, item in enumerate(raw if isinstance(raw, list) else [], 1):
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or "").strip()
            if not claim:
                continue
            items.append({
                "id": str(item.get("id") or f"H{index}"),
                "claim": claim,
                "verify_by": str(item.get("verify_by") or ""),
                "check": str(item.get("check") or ""),
            })
        if items:
            return items
        try:
            from freight.answerability import _match
            hits = _match(self.system, self.question)
            subjects = (list((hits.get("fee_types") or {}).keys())
                        + list((hits.get("types") or {}).keys()))
        except Exception:  # noqa: BLE001
            subjects = []
        subject = subjects[0] if subjects else self.object_type
        return [
            {"id": "H1",
             "claim": f"问句涉及的 {subject} 在本体里有可计算的声明",
             "verify_by": "tbox.describe 返回的声明 ID 与参数",
             "check": "declaration_hit"},
            {"id": "H2",
             "claim": f"问句涉及的 {subject} 有可核对的业务事实与证据",
             "verify_by": "function.evidence_chain 的证据与 checksum",
             "check": "evidence_complete"},
            {"id": "H3",
             "claim": f"现有声明与事实足以回答：{self.question[:40]}",
             "verify_by": "function.fee_breakdown 的 rule_delta",
             "check": "rule_delta"},
        ]

    def _tbox_step(self, items: list[dict], query_inputs: dict,
                   plan: dict, index: int) -> dict:
        counts: dict[str, int] = {}
        for item in items:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        tbox_query = self._tbox_query(query_inputs)
        return {
            "index": index,
            "title": "先查本体，圈定要看的类型",
            "tag": "TBox · 问答前置",
            "actor": "agent",
            "query": {
                "label": "业务问题 → 本体查询",
                "expression": tbox_query,
                "api": "semantic_layer.json · describe(types, fee_types)",
                "params": query_inputs,
                "input": query_inputs,
            },
            "layers": {
                "question": self.question,
                "tbox": {
                    "source": "semantic_layer.json · 按业务问题自动推导",
                    "query": tbox_query,
                    "query_explain": (
                        "大白话：先拿业务问题查本体字典，只看有哪些类型、"
                        "哪些关系、哪些规则和价格参数，不碰任何业务数据。"
                    ),
                    "returned": self._tbox_returned(items),
                    "returned_explain": (
                        "大白话：拿到的全是定义——规则怎么算、关系怎么连，"
                        "还没有这票运单的任何金额和状态。"
                    ),
                    "items": items,
                    "note": (
                        "本步发生在任何 MCP 业务查询之前；规则、关系由本体图"
                        "按类型自动推导，不预先指定 ID。"
                    ),
                },
                "abox": {
                    "source": "尚未访问业务系统",
                    "query": "—（本步只查本体，不取业务事实）",
                    "query_explain": (
                        "大白话：这一步还没问运单、费用项、结算单，"
                        "所以没有任何实际金额。"
                    ),
                    "returned": "0 行；未装入任何业务对象",
                    "returned_explain": (
                        "大白话：没有业务数据，自然也没有‘规则说多少、实际收多少’的差额。"
                    ),
                    "items": [],
                    "note": "ABox 查询从下一步才开始。",
                },
                "join": (
                    "先由业务问题圈定 TBox 范围；ABox 尚未开始取数，"
                    "因此本步只能回答‘该看什么’，不能回答‘实际发生了什么’。"
                ),
            },
            "operations": [
                f"接收业务问题：{self.question}",
                f"按问题选择类型：{'、'.join(query_inputs['types']) or '无'}",
                f"调用本体 describe()：{'、'.join(query_inputs['fee_types']) or '未指定费用类型'}",
                "由本体自动推导关系、规则、动作和定价参数，不指定规则或关系 ID",
            ],
            "data": {"datasets": [{
                "caption": "本体查询返回 · 按业务问题圈定",
                "columns": [
                    {"key": "kind", "label": "类别"},
                    {"key": "id", "label": "本体 ID"},
                    {"key": "label", "label": "名称"},
                    {"key": "detail", "label": "定义"},
                ],
                "rows": items,
                "note": "本步没有业务数据；下一步才开始 MCP 查询",
            }]},
            "facts": [
                {"label": "业务问题", "value": self.question},
                {"label": "圈定类型", "value": str(len(query_inputs["types"]))},
                {"label": "类定义", "value": str(counts.get("class", 0))},
                {"label": "关系", "value": str(counts.get("relation", 0))},
                {"label": "规则", "value": str(counts.get("rule", 0))},
                {"label": "动作", "value": str(counts.get("action", 0))},
                {"label": "定价参数", "value": str(counts.get("pricing", 0))},
                {"label": "业务事实", "value": "0 行（尚未查询）"},
            ],
            "checks": [
                {"pass": bool(self.question), "label": "业务问题非空",
                 "expected": "有明确问题", "actual": self.question or "空"},
                {"pass": bool(query_inputs["types"]), "label": "圈定类型非空",
                 "expected": "≥1 个类型",
                 "actual": "、".join(query_inputs["types"]) or "无"},
                {"pass": True, "label": "本步未访问业务系统",
                 "expected": "0 行业务事实", "actual": "0 行"},
            ],
            "judgment": (
                f"业务问题涉及 {'、'.join(query_inputs['types']) or '未知类型'}；"
                f"本体自动带出 {counts.get('rule', 0)} 条作用域规则和 "
                f"{counts.get('pricing', 0)} 个定价参数。"
                f"圈定理由：{plan.get('reason') or '按业务问题匹配类型'}"
            ),
            "conclusion": (
                "这一步只确定‘该按哪些本体规则看’，没有任何业务金额；"
                "金额必须从下一步的业务查询里取。"
            ),
            "next": "带着本体范围去业务系统定位运单、费用项、结算和证据。",
            "graph": self._graph([]),
            "source": "TBox：semantic_layer.json；ABox：未查询",
            "basis": " · ".join(
                f'{item["id"]} {item["label"]}' for item in items[:6]
            ) or "本体定义",
            "ok": True,
            "elapsed_ms": 0,
        }

    # ------------------------------------------------------------------ 工具步骤
    def _run_tool_step(self, action: dict, tool: str, arguments: dict,
                       index: int) -> tuple[dict, dict]:
        result: Any = None
        error: str | None = None
        elapsed_ms = 0
        if tool not in self.catalog:
            error = f"工具 {tool or '（空）'} 不在 MCP 工具目录中"
        else:
            started = time.perf_counter()
            try:
                payload = self.facts.call(tool, arguments)
                result = payload.get("result")
            except KeyError as exc:
                error = f"参数不完整：缺少 {exc}"
            except BusinessReject as exc:
                error = f"{exc.code}: {exc}"
                if exc.details:
                    error += f" | {json.dumps(exc.details, ensure_ascii=False)}"
            except Exception as exc:
                error = str(exc)
            elapsed_ms = round((time.perf_counter() - started) * 1000)

        meta = self.catalog.get(tool, {})
        rows = self._row_count(result)
        counts = self._counts(result)
        tbox_items, tbox_query_inputs = self._tbox_items(tool, result, arguments)
        # 计划对照：本步服务了规划里的哪条取证任务（计划外调用 = None）
        serves = self._task_for_tool(tool)
        observation = {
            "tool": tool,
            "arguments": arguments,
            "ok": error is None,
            "rows": rows,
            "error": error,
            "result": result,
            "serves": serves["intent"] if serves else None,
        }
        question = action.get("question") or f"调用 {tool} 获取诊断所需事实"
        thought = action.get("thought") or ""
        if error:
            conclusion = f"工具调用失败：{error}"
            judgment = "本步没有拿到业务事实，Agent 将根据错误信息调整工具或参数。"
        else:
            conclusion = (
                f"返回 {rows} 行；对象 {counts['objects']} 个、关系 {counts['relations']} 条、"
                f"证据 {counts['evidence']} 条。"
            )
            judgment = thought or "根据返回结果判断下一步是否需要补充查询。"
        abox_items = [{
            "kind": "tool",
            "id": tool or "unknown",
            "label": f'{meta.get("system", "MCP")} · {meta.get("description", tool)}',
            "detail": (
                f'{json.dumps(arguments, ensure_ascii=False)} · '
                + (f"返回 {rows} 行" if not error else f"失败：{error}")
            ),
        }]
        if error is None:
            abox_items.append({
                "kind": "memory",
                "id": f"mem:{tool}",
                "label": "装入内存实例图",
                "detail": (
                    f"对象 {counts['objects']} · 关系 {counts['relations']} · "
                    f"证据 {counts['evidence']}"
                ),
            })
        tbox_note = (
            "按 ABox 返回对象的类型查询语义层：类定义、邻接关系、作用域规则、定价参数；"
            "查询只给类型，不预先指定规则或关系 ID。"
            if tbox_items else
            "本步返回的对象类型已在前面步骤查过，没有新增 TBox 定义。"
        )
        join = self._join_text(tbox_items, counts, error)
        tbox_query = self._tbox_query(tbox_query_inputs)
        tbox_returned = self._tbox_returned(tbox_items)
        abox_query = f'{tool or "(invalid)"}({json.dumps(arguments, ensure_ascii=False)})'
        abox_returned = self._abox_returned(tool, result, rows, error)
        tbox_query_explain = self._tbox_query_explain(tbox_query_inputs)
        tbox_returned_explain = self._tbox_returned_explain(tbox_items)
        abox_query_explain = self._abox_query_explain(tool)
        abox_returned_explain = self._abox_returned_explain(
            tool, result, rows, error
        )
        step = {
            "index": index,
            "title": action.get("title") or meta.get("name") or (tool or "工具调用"),
            "tag": f"Agent · {tool or 'invalid'}",
            "actor": "agent",
            "query": {
                "label": "Agent 本步问题 → MCP 工具调用",
                "expression": f'{tool or "(invalid)"}({json.dumps(arguments, ensure_ascii=False)})',
                "api": f'/freight/mcp/call · {meta.get("system", "MCP")}',
                "params": arguments,
            },
            "layers": {
                "question": question,
                "tbox": {
                    "source": "semantic_layer.json · classes / relations / rules / pricing",
                    "query": tbox_query,
                    "query_explain": tbox_query_explain,
                    "returned": tbox_returned,
                    "returned_explain": tbox_returned_explain,
                    "items": tbox_items,
                    "note": tbox_note,
                },
                "abox": {
                    "source": "MCP 工具实时查询 · 内存实例图",
                    "query": abox_query,
                    "query_explain": abox_query_explain,
                    "returned": abox_returned,
                    "returned_explain": abox_returned_explain,
                    "items": abox_items,
                    "note": (
                        f'{meta.get("returns", "业务事实")}；'
                        + ("查询失败，未装入内存图。" if error
                           else f"返回 {rows} 行，按对象 ID 去重后装入内存。")
                    ),
                },
                "join": join,
            },
            "operations": [
                f"Agent 提出问题：{question}",
                f"调用 MCP 工具 {tool or '(invalid)'}，参数 {json.dumps(arguments, ensure_ascii=False)}",
                "按 type / predicate 反查 TBox 定义，再与 ABox 返回事实在内存中对齐",
            ],
            "data": self._dataset(tool, result),
            "facts": [
                {"label": "工具", "value": tool or "—"},
                {"label": "返回行数", "value": str(rows)},
                {"label": "服务任务", "value": (serves["intent"] if serves
                                            else "计划外调用")},
                {"label": "耗时", "value": f"{elapsed_ms} ms"},
            ],
            "rows": rows,
            "checks": [],
            "judgment": judgment,
            "conclusion": (
                conclusion + (f"（服务任务：{serves['intent']}）" if serves
                             else "（计划外调用）")
            ),
            "next": "由 Agent 根据本步观察决定下一步",
            "graph": self._graph(result),
            "source": (
                f'ABox：{tool or "—"} 返回 {rows} 行；'
                "TBox：semantic_layer.json 只提供类、关系、规则与定价定义。"
            ),
            "basis": " · ".join(
                f'{item["id"]} {item["label"]}' for item in tbox_items[:6]
            ) or "本步未新增本体定义",
            "tool": tool,
            "arguments": arguments,
            "ok": error is None,
            "elapsed_ms": elapsed_ms,
        }
        return step, observation

    # ------------------------------------------------------------------ 最终步骤
    def _action_step(self, plan: dict, index: int) -> dict:
        """第 5 步：动作提案 + 前置校验，不执行任何写操作。"""
        empty_result = [
            {"object_id": plan.get("waybill_id"), "type": "Waybill"},
            {"object_id": plan.get("wait_fee_id"), "type": "FeeItem"},
            {"object_id": plan.get("settlement_id"), "type": "Settlement"},
            {"object_id": plan.get("invoice_id"), "type": "Invoice"},
        ]
        tbox_items, query_inputs = self._tbox_items(
            "action.plan", empty_result, {}
        )
        actions = plan.get("actions") or []
        auto_actions = plan.get("auto_actions") or []
        allowed = [item for item in actions if item.get("allowed")]
        readback = plan.get("readback") or []
        downstream = plan.get("downstream") or []
        abox_items = [{
            "kind": "action",
            "id": item["action_id"],
            "label": item["label"],
            "detail": (
                f'{item["target"]} · {item["target_status"]} · '
                f'{item.get("role") or "—"} · '
                + ("前置条件满足" if item["allowed"] else f'阻断：{item["blocked_reason"]}')
            ),
        } for item in actions]
        abox_items.extend({
            "kind": "action",
            "id": item["action_id"],
            "label": item["label"],
            "detail": f'{item["target"]} · 自动联动 · {item["reason"]}',
        } for item in auto_actions)
        abox_items.extend({
            "kind": "row",
            "id": item["object_id"],
            "label": f'{item["type_label"]} {item["status"]}',
            "detail": item["value"],
        } for item in readback)
        dataset_rows = [{
            "action_id": item["action_id"],
            "label": item["label"],
            "target": item["target"],
            "target_status": item["target_status"],
            "role": item.get("role") or "—",
            "allowed": "是" if item["allowed"] else f'否 · {item["blocked_reason"]}',
        } for item in actions]
        dataset_rows.extend({
            "action_id": item["action_id"],
            "label": item["label"],
            "target": item["target"],
            "target_status": "自动联动",
            "role": "—",
            "allowed": "自动联动",
        } for item in auto_actions)
        baseline_rows = [{
            "object_id": item["object_id"],
            "type_label": item["type_label"],
            "status": item["status"],
            "value": item["value"],
        } for item in readback]
        chain = " → ".join(
            [f'{item["label"]}(已完成)' for item in plan.get("prerequisites") or []]
            + [f'{item["label"]}({item["action_id"]})' for item in actions]
            + [f'{item["label"]}(自动)' for item in auto_actions]
        ) or "无待执行动作"
        expected = plan.get("wait_expected", 0.0)
        actual = plan.get("wait_actual", 0.0)
        delta = plan.get("wait_delta", 0.0)
        waive = plan.get("waive_amount", 0.0)
        graph = {
            "nodes": [],
            "edges": [],
        }
        node_specs = [
            (plan.get("wait_fee_id"), "FeeItem", "实收 %.2f" % actual),
            (plan.get("settlement_id"), "Settlement", "POSTED"),
            (plan.get("invoice_id"), "Invoice", "ISSUED"),
            (plan.get("shipper_id"), "Shipper", "ACTIVE"),
            (plan.get("exception_id"), "ExceptionTicket", "OPEN"),
        ]
        for object_id, type_id, status in node_specs:
            if not object_id:
                continue
            graph["nodes"].append({
                "id": object_id,
                "type_label": (self.sem.classes.get(type_id) or {}).get("label", type_id),
                "status": status,
            })
        if plan.get("wait_fee_id") and plan.get("settlement_id"):
            graph["edges"].append({
                "from": plan["wait_fee_id"],
                "to": plan["settlement_id"],
                "label": "汇总进结算单",
            })
        if plan.get("settlement_id") and plan.get("invoice_id"):
            graph["edges"].append({
                "from": plan["settlement_id"],
                "to": plan["invoice_id"],
                "label": "生成发票",
            })
        if plan.get("invoice_id") and plan.get("shipper_id"):
            graph["edges"].append({
                "from": plan["invoice_id"],
                "to": plan["shipper_id"],
                "label": "开给货主",
            })
        checks = [{
            "pass": item["allowed"],
            "label": f'{item["label"]} · {item["action_id"]}',
            "expected": f'{item.get("role") or "—"} 可执行',
            "actual": "可执行" if item["allowed"] else f'阻断：{item["blocked_reason"]}',
        } for item in actions]
        checks.append({
            "pass": waive <= actual,
            "label": "减免金额不超过实收",
            "expected": f"≤ {actual:.2f}",
            "actual": f"{waive:.2f}",
        })
        checks.append({
            "pass": all(
                item.get("payload", {}).get("amount") in (None, waive)
                for item in actions
            ),
            "label": "冲正金额与减免金额一致",
            "expected": f"{waive:.2f}",
            "actual": f'{waive:.2f}',
        })
        return {
            "index": index,
            "title": "提动作，等人工确认",
            "tag": "动作 · 权限 / 前置校验",
            "actor": "agent",
            "query": {
                "label": "本体动作定义 + 对象状态 → 动作提案",
                "expression": 'action.plan({"waybill_id": "%s"})' % plan.get("waybill_id"),
                "api": "semantic_layer.json · actions + 对象状态",
                "params": {"waybill_id": plan.get("waybill_id")},
            },
            "layers": {
                "question": "这笔差额该走哪些动作？前置条件满足吗？需要什么角色确认？",
                "tbox": {
                    "source": "semantic_layer.json · 动作定义（按对象类型自动推导）",
                    "query": self._tbox_query(query_inputs),
                    "query_explain": (
                        "大白话：查本体里费用项、结算单、发票允许做哪些动作、"
                        "从什么状态能执行、需要什么角色。"
                    ),
                    "returned": self._tbox_returned(tbox_items),
                    "returned_explain": (
                        "大白话：拿到的只是动作规则，例如“减免要从争议状态发起、"
                        "需要财务确认”，仍然不含任何业务数据。"
                    ),
                    "items": tbox_items,
                    "note": "动作、角色、前置状态都来自本体动作定义；是否可执行再由 ABox 对象状态判定。",
                },
                "abox": {
                    "source": "动作提案 + 对象当前状态 + 影响面",
                    "query": f'action.plan({{"waybill_id": "{plan.get("waybill_id")}"}})',
                    "query_explain": (
                        "大白话：看这笔钱现在挂在哪个费用项、哪张结算单和发票上，"
                        "再逐个动作检查前置状态和权限。"
                    ),
                    "returned": (
                        f'候选动作 {len(actions)} 个、可执行 {len(allowed)} 个、'
                        f'自动联动 {len(auto_actions)} 个；'
                        f'影响下游 {len(downstream)} 个对象；基线回读 {len(readback)} 个对象。'
                    ),
                    "returned_explain": (
                        "大白话：这里只列建议动作和当前状态，没有改任何业务数据；"
                        "必须人工点确认后才会真正执行。"
                    ),
                    "items": abox_items,
                    "note": "Agent 只提案不执行；动作由人工确认后经 REST 接口按角色、状态机和幂等键执行。",
                },
                "join": (
                    "TBox 给动作定义与允许角色，ABox 给对象当前状态与金额；"
                    "两边一对，才知道哪个动作现在能做、做完影响谁。"
                ),
            },
            "operations": [
                "按差额定位要处理的费用项",
                f"沿费用 → 结算 → 发票 → 货主关系计算 {len(downstream)} 个下游对象",
                "逐个动作校验角色、前置状态和幂等键",
                "生成动作提案并等待人工确认",
            ],
            "data": {"datasets": [
                {
                    "caption": "动作提案 · 未执行",
                    "columns": [
                        {"key": "action_id", "label": "动作"},
                        {"key": "label", "label": "名称"},
                        {"key": "target", "label": "对象"},
                        {"key": "target_status", "label": "当前状态"},
                        {"key": "role", "label": "建议角色"},
                        {"key": "allowed", "label": "可执行"},
                    ],
                    "rows": dataset_rows,
                    "note": "动作均未执行，等待人工确认；自动联动项由冲正触发",
                },
                {
                    "caption": "动作执行前基线 · 回读对象",
                    "columns": [
                        {"key": "object_id", "label": "对象"},
                        {"key": "type_label", "label": "类型"},
                        {"key": "status", "label": "状态"},
                        {"key": "value", "label": "当前金额 / 说明"},
                    ],
                    "rows": baseline_rows,
                    "note": "执行后页面会重新回读这组对象并对比",
                },
            ]},
            "facts": [
                {"label": "规则应计", "value": f"{expected:.2f}"},
                {"label": "实际收取", "value": f"{actual:.2f}"},
                {"label": "多收金额", "value": f"{delta:.2f}"},
                {"label": "建议减免", "value": f"{waive:.2f}"},
                {"label": "候选动作", "value": str(len(actions))},
                {"label": "可执行", "value": str(len(allowed))},
            ],
            "checks": checks,
            "judgment": (
                f"等候费按规则应计 {expected:.2f} 元、实际收取 {actual:.2f} 元，"
                f"多收 {delta:.2f} 元；动作链：{chain}。"
            ),
            "conclusion": (
                f"{len(allowed)}/{len(actions)} 个动作前置条件满足。"
                "这只是提案：未人工确认前不会写业务数据，也不会改结算或发票状态。"
            ),
            "next": "由财务角色确认后依次执行；执行完回读费用项、结算单、发票和工单状态。",
            "graph": graph,
            "source": "ABox：对象状态 + 影响面；TBox：动作定义、角色与状态机",
            "basis": " · ".join(
                f'{item["id"]} {item["label"]}' for item in tbox_items
                if item["kind"] in ("action", "rule")
            ) or "本体动作定义",
            "proposal": plan,
            "ok": True,
            "elapsed_ms": 0,
        }

    def _final_step(self, action: dict, observations: list[dict],
                    index: int, error: str | None,
                    plan: dict | None = None) -> dict:
        analysis = self.system.fee_analysis(self.object_id)
        tbox_items = list(self.tbox_seen.values())
        tbox_query = self._tbox_query({
            "types": sorted(self.types_seen),
            "fee_types": sorted(self.fee_types_seen),
        })
        tbox_query_explain = self._tbox_query_explain({
            "types": sorted(self.types_seen),
            "fee_types": sorted(self.fee_types_seen),
        })
        tbox_returned_explain = self._tbox_returned_explain(tbox_items)
        abox_items: list[dict] = []
        for tool in sorted({obs["tool"] for obs in observations if obs.get("tool")}):
            rows = sum(obs.get("rows", 0) for obs in observations if obs["tool"] == tool)
            meta = self.catalog.get(tool, {})
            abox_items.append({
                "kind": "tool",
                "id": tool,
                "label": meta.get("description", tool),
                "detail": f'累计 {sum(1 for o in observations if o["tool"] == tool)} 次 · {rows} 行',
            })
        datasets = []
        facts = []
        checks = []
        traceability = None
        if analysis:
            version = analysis.get("ontology_version")
            traceability = analysis.get("traceability")
            breakdown = analysis.get("breakdown") or []
            datasets.append({
                "caption": "最终金额核对 · fee_analysis 汇总",
                "columns": [
                    {"key": "fee_id", "label": "费用项"},
                    {"key": "label", "label": "费用名称"},
                    {"key": "quoted_amount", "label": "报价"},
                    {"key": "expected_amount", "label": "规则应计"},
                    {"key": "actual_amount", "label": "实收"},
                    {"key": "rule_delta", "label": "规则差额"},
                    {"key": "status", "label": "状态"},
                ],
                "rows": breakdown[:10],
                "note": "运单尚未产生费用项" if not breakdown else "",
            })
            waiting_rule = analysis.get("waiting_rule") or {}
            tariffs = waiting_rule.get("tariffs") or []
            if tariffs:
                selected = waiting_rule.get("vehicle_type") or "未识别"
                datasets.append({
                    "caption": f"等候费车型口径对照 · 当前运单车型 {selected}",
                    "columns": [
                        {"key": "vehicle_type", "label": "车型"},
                        {"key": "free_minutes", "label": "免费分钟"},
                        {"key": "block_minutes", "label": "每块分钟"},
                        {"key": "per_block", "label": "每块金额"},
                        {"key": "blocks", "label": "计费块数"},
                        {"key": "expected", "label": "应计"},
                        {"key": "selected", "label": "当前口径"},
                    ],
                    "rows": [{
                        **tariff,
                        "selected": "是" if tariff.get("selected") else "",
                    } for tariff in tariffs],
                    "note": "同一等候时长套用不同车型费率；当前运单只取所选车型口径",
                })
            evidence = analysis.get("evidence") or []
            if evidence:
                datasets.append({
                    "caption": "证据链 · 每条证据带 checksum",
                    "columns": [
                        {"key": "evidence_id", "label": "证据"},
                        {"key": "evidence_type", "label": "类型"},
                        {"key": "source_system", "label": "来源系统"},
                        {"key": "version", "label": "源版本"},
                        {"key": "checksum", "label": "sha256 摘要"},
                        {"key": "summary", "label": "说明"},
                    ],
                    "rows": [{
                        "evidence_id": ev.get("evidence_id"),
                        "evidence_type": ev.get("evidence_type"),
                        "source_system": ev.get("source_system"),
                        "version": ev.get("version"),
                        "checksum": ev.get("checksum"),
                        "summary": ev.get("summary"),
                    } for ev in evidence],
                    "note": "证据只读；摘要用于回查源系统与证明结论未被篡改",
                })
            abox_items.append({
                "kind": "sql",
                "id": "fee_analysis",
                "label": "最终金额核对（服务端汇总）",
                "detail": (
                    f'费用项 {len(breakdown)} · 预估 {analysis.get("quoted_total", 0):.2f}'
                    f' · 实收 {analysis.get("actual_total", 0):.2f}'
                    f' · 差额 {analysis.get("variance", 0):+.2f}'
                ),
            })
            facts = [
                {"label": "本体版本", "value": str(version or "—")},
                {"label": "状态", "value": str(analysis.get("status", "—"))},
                {"label": "预估", "value": f'{analysis.get("quoted_total", 0):.2f}'},
                {"label": "实收", "value": f'{analysis.get("actual_total", 0):.2f}'},
                {"label": "差额", "value": f'{analysis.get("variance", 0):+.2f}'},
                {"label": "发现", "value": str(len(analysis.get("findings") or []))},
                {"label": "证据", "value": str(len(analysis.get("evidence") or []))},
            ]
            checks = [{
                "pass": finding.get("severity") == "OK",
                "label": finding.get("rule_id") or finding.get("code", "规则核对"),
                "expected": finding.get("expected", "—"),
                "actual": finding.get("actual", "—"),
            } for finding in (analysis.get("findings") or [])[:4]]
            checks.append({
                "pass": bool(version),
                "label": "结论绑定本体版本",
                "expected": "版本号存在",
                "actual": version or "缺失",
            })
            checks.append({
                "pass": all(ev.get("checksum") for ev in evidence),
                "label": "证据 checksum 完整",
                "expected": f"{len(evidence)} 条",
                "actual": f"{sum(1 for ev in evidence if ev.get('checksum'))} 条",
            })
        if plan:
            readback = plan.get("readback") or []
            datasets.append({
                "caption": "业务事实回读 · 动作执行前基线",
                "columns": [
                    {"key": "object_id", "label": "对象"},
                    {"key": "type_label", "label": "类型"},
                    {"key": "status", "label": "状态"},
                    {"key": "value", "label": "金额 / 说明"},
                ],
                "rows": readback,
                "note": "动作执行后由页面重新回读同一组对象，对比状态与金额变化",
            })
            abox_items.append({
                "kind": "memory",
                "id": "memory:readback",
                "label": "业务事实回读基线",
                "detail": (
                    f'费用项 · 结算单 · 发票 · 工单 · 货主 共 {len(readback)} 个对象'
                ),
            })
            facts.extend([
                {"label": "待确认动作", "value": str(len(plan.get("actions") or []))},
                {"label": "影响下游", "value": str(len(plan.get("downstream") or []))},
            ])
            checks.append({
                "pass": all(item.get("allowed") for item in plan.get("actions") or []),
                "label": "动作前置条件全部满足",
                "expected": "全部可执行",
                "actual": (
                    f'{sum(1 for i in plan.get("actions") or [] if i.get("allowed"))}'
                    f'/{len(plan.get("actions") or [])}'
                ),
            })
        graph = self._graph([obs.get("result") for obs in observations if obs.get("ok")])
        if graph["nodes"]:
            abox_items.append({
                "kind": "memory",
                "id": "memory:graph",
                "label": "最终内存实例图",
                "detail": f'节点 {len(graph["nodes"])} · 边 {len(graph["edges"])}',
            })
        # ── 收口三件套（全部确定性产出，模型只能叙述不能改写）────────────
        closing = self._closing_datasets()
        datasets.extend(closing["datasets"])
        facts.extend(closing["facts"])
        checks.extend(closing["checks"])
        question = action.get("question") or "现有 ABox 观察是否足以支撑诊断结论？"
        thought = action.get("thought") or ""
        answer = action.get("answer") or "Agent 未给出最终结论，请查看前序观察数据。"
        note = (
            "TBox 只提供类、关系、规则与定价参数；金额、证据、结算状态全部来自 ABox。"
        )
        if error:
            note += f' Agent 异常：{error}'
        total_rows = sum(obs.get("rows", 0) for obs in observations)
        tool_count = len({obs["tool"] for obs in observations if obs.get("tool")})
        if analysis:
            abox_returned = (
                f'汇总 {tool_count} 个工具查询、{len(breakdown)} 个费用项：'
                f'预估 {analysis.get("quoted_total", 0):.2f}'
                f' · 实收 {analysis.get("actual_total", 0):.2f}'
                f' · 差额 {analysis.get("variance", 0):+.2f}'
            )
        else:
            abox_returned = (
                f'汇总 {len(observations)} 次 MCP 调用，共 {total_rows} 行业务事实。'
            )
        return {
            "index": index,
            "title": action.get("title") or (
                "回读业务事实 · 诊断结论" if plan else "综合判断与结论"
            ),
            "tag": "Agent · Final",
            "actor": "agent",
            "query": {
                "label": "Agent 最终核对 → 输出结论",
                "expression": "final()",
                "api": "LLM 综合推理",
                "params": {},
            },
            "layers": {
                "question": question,
                "tbox": {
                    "source": (
                        f"semantic_layer.json · v{analysis.get('ontology_version')} · 全程引用汇总"
                        if analysis else "semantic_layer.json · 全程引用汇总"
                    ),
                    "query": tbox_query,
                    "query_explain": tbox_query_explain,
                    "returned": self._tbox_returned(tbox_items),
                    "returned_explain": tbox_returned_explain,
                    "items": tbox_items,
                    "note": "按全程出现过的对象类型再做一次本体查询，返回类、邻接关系、作用域规则与定价参数。",
                },
                "abox": {
                    "source": "MCP 工具结果 + fee_analysis 汇总 · 内存实例图",
                    "query": f'final() · 汇总 {len(observations)} 次 MCP 调用',
                    "query_explain": "大白话：把前面从业务系统查到的真实数据汇总起来，和本体规则逐项对照。",
                    "returned": abox_returned,
                    "returned_explain": (
                        "大白话：规则说该收多少、系统实际收了多少，"
                        "两边相减就是问题金额。"
                    ),
                    "items": abox_items,
                    "note": note,
                },
                "join": (
                    "TBox 的类与关系决定对象如何拼接，规则与定价决定金额如何核对；"
                    "ABox 给出实际金额、证据与状态；两层在内存中对齐后形成结论。"
                ),
            },
            "operations": [
                f"汇总 {len(observations)} 次 MCP 工具返回",
                "按 TBox 关系把对象装入内存实例图",
                "用规则与定价参数核对金额、证据与状态",
            ],
            "data": {"datasets": datasets},
            "facts": facts,
            "checks": checks,
            "judgment": thought or "基于前序 ABox 观察与 TBox 规则输出最终诊断。",
            "conclusion": answer,
            "ontology_version": analysis.get("ontology_version") if analysis else None,
            "traceability": traceability,
            "next": (
                "人工确认动作后，页面会重新回读费用项、结算单、发票和工单，"
                "对比状态与金额变化；未确认前业务数据保持不变。"
                if plan else "诊断结束；如需复核可重新运行 Agent"
            ),
            "graph": graph,
            "source": "ABox：MCP 工具返回 + fee_analysis 汇总；TBox：semantic_layer.json",
            "basis": " · ".join(
                f'{item["id"]} {item["label"]}' for item in tbox_items[:8]
            ) or "全程未引用额外本体定义",
            "ok": error is None,
            "elapsed_ms": 0,
        }

    # ------------------------------------------------------------------ Prompt
    def _system_prompt(self) -> str:
        tools = [
            f'{tool["name"]}|{tool.get("system", "")}|{tool.get("description", "")}'
            f'|参数={json.dumps(tool.get("arguments", {}), ensure_ascii=False)}'
            f'|返回={tool.get("returns", "")}'
            for tool in self.catalog.values()
        ]
        if self.question_kind == "knowledge":
            diagnosis_rule = (
                "本问题已判定为**本体知识型**：答案在 TBox 声明里——规则公式、"
                "定价参数、动作与角色、状态机迁移。先用 tbox.describe"
                "（费用类问题给 fee_types）读取规则与参数，状态流转问题用 "
                "ontology.state_machine；拿到声明即可给 final 结论，"
                "不要查询业务实例（ontology.query / search_around / function.* "
                "都不需要），也不要把问题改写成对某张运单的取证。"
            )
        else:
            diagnosis_rule = (
                "确定性函数（function.*）是问题无关的计算原语：费用拆解、"
                "报价对账、证据链、动作预检应优先调用它们，不要用手工多轮查询"
                "重新实现。凡是费用构成、金额差异、为什么多收/少收类问题，"
                "第一次业务查询必须调用 function.fee_breakdown，拿到确定性拆解"
                "后再决定是否补充查询。"
            )
            if self.question_kind == "mixed":
                diagnosis_rule += (
                    "本问题同时含知识成分：规则口径与参数直接引用 TBox 声明"
                    "（tbox.describe 返回的规则公式与定价参数），不要为口径"
                    "再去查业务实例。"
                )
        diagnosis_rule += (
            "动作只能调用 action.plan 做预检，禁止执行 action.execute。"
        )
        return f"""你是货拉拉运单费用诊断 Agent。你运行在本体系统内，只读诊断，不修改任何业务数据。
任务：围绕业务问题「{self.question}」，针对运单 {self.waybill_id}（对象 ID {self.object_id}，对象类型 {self.object_type}），通过通用本体查询原语查询业务事实（ABox），结合本体定义（TBox）解释费用构成、差异原因、证据充分性和动作影响面，最后给出可核对的结论。
约束：不要假设目录之外的任何本体 ID；当前工具目录已按对象类型裁剪，优先在对象能力集内选择工具。系统已优先注入全量紧凑 TBox 目录；装不下时才按 object type group 缩域。目录只是索引：需要类型间路径时用 ontology.path，需要字段/关系/规则时用 tbox.describe；需要从锚点对象展开邻居时用 ontology.search_around，不要预计算全局强依赖闭包。所有查询入口都会做 schema 校验，报 SCHEMA_ERROR 时按返回的 allowed values 重写查询。
不要用关键词规则给问题打意图标签；按工具契约选择：对象类型、必填参数、返回类型和权限是否匹配。用户给出的金额或差异是待核对断言，不是过滤条件；先用确定性函数算出实际值，再确认或纠正用户说的数字。
{diagnosis_rule}

【标识约定】
- 本运单对象 ID = {self.object_id}；所有查询参数里的 waybill_id / object_id 都传这个完整对象 ID。
- 裸编号 {self.waybill_id} 只用于展示和搜索关键字，不要直接传给工具。

【本体目录与检索工具（TBox，按需调用，禁止编造）】
ontology.catalog|读取紧凑 TBox 目录；全量装得下直接返回，装不下只返回业务域索引|参数={{"group_ids":["<业务域 ID，可空>"],"budget":6000}}
ontology.object_type_search|按对象类型 ID、名称、描述、别名和业务域召回锚点类型|参数={{"question":"<自然语言问题>","top_k":8,"group_ids":["<业务域 ID，可空>"]}}
ontology.schema|读取完整 schema，结构扩展固定沿强依赖白名单执行|参数={{"question":"<自然语言问题，可空>","types":["<类型，可空>"],"top_k":12,"depth":2}}
ontology.path|查询两个类型之间所有合法关系路径|参数={{"from_type":"<类型>","to_type":"<类型>","max_depth":4,"limit":10}}
ontology.state_machine|读取状态机声明：状态集、起点/终态、合法迁移边（带动作与角色）；给 object_id 时附当前状态与可用迁移|参数={{"object_type":"<类型 ID，可空>","object_id":"<对象 ID，可空>"}}
ontology.search_around|从一组锚点对象沿 link type 按需展开实例邻居，不预计算全局闭包|参数={{"object_ids":["<对象 ID>"],"link_type_ids":["<关系 ID，可空>"],"direction":"both","depth":1,"limit":200}}
tbox.describe|按类型读取类定义、邻接关系、作用域规则、动作和定价参数|参数={{"types":["<目录中的类型 ID>"],"fee_types":["<目录中的费用类型 ID>"]}}
- 对话开头已注入紧凑 TBox 目录；能覆盖问题范围时直接使用，不要重复请求全量目录。
- 目录没有覆盖时，先用 ontology.object_type_search 找锚点类型，再用 tbox.describe 读取定义。
- 需要沿对象关系取证时，用 ontology.search_around 从已知对象 ID 出发按 link type 展开，不要手工拼全局闭包。
- 只传真正需要的类型，不要一次请求全量目录。

【MCP 工具目录（ABox 唯一事实来源）】
{chr(10).join(tools)}

【输出协议】
每次回复只输出一个 JSON 对象，不要 Markdown 代码围栏，不要任何多余文字。
需要找对象类型锚点时：
{{"action":"tool","title":"12字内步骤名","question":"这一步向本体确认的检索问题","thought":"为什么需要检索","tool":"ontology.object_type_search","arguments":{{"question":"...","top_k":8}}}}
需要从锚点对象沿关系展开时：
{{"action":"tool","title":"12字内步骤名","question":"这一步要展开的关系","thought":"为什么需要展开","tool":"ontology.search_around","arguments":{{"object_ids":["..."],"link_type_ids":["..."],"direction":"both","depth":1}}}}
需要完整 schema 时：
{{"action":"tool","title":"12字内步骤名","question":"这一步向本体确认的 schema","thought":"为什么需要 schema","tool":"ontology.schema","arguments":{{"question":"...","top_k":12,"depth":2}}}}
需要查询类型间路径时：
{{"action":"tool","title":"12字内步骤名","question":"这一步确认的类型关系","thought":"为什么需要路径","tool":"ontology.path","arguments":{{"from_type":"...","to_type":"...","max_depth":4}}}}
需要查本体定义时：
{{"action":"tool","title":"12字内步骤名","question":"这一步向本体确认的定义","thought":"为什么需要这一步","tool":"tbox.describe","arguments":{{"types":["..."],"fee_types":["..."]}}}}
需要查业务事实时：
{{"action":"tool","title":"12字内步骤名","question":"这一步向业务系统确认的具体问题","thought":"为什么需要这一步","tool":"ontology.query","arguments":{{"type":"<类型>","where":{{...}},"limit":100}}}}
完成诊断时：
{{"action":"final","title":"结论","question":"最后核对的问题","thought":"综合判断过程","answer":"给业务人员的最终结论，必须引用具体对象 ID、金额、证据 ID"}}

【规则】
1. question 必须是自然语言、具体、可核对的问题，禁止空泛。
2. 先定位运单，再按需要查费用项、关系、证据、结算、异常；根据实际返回决定下一步，不要机械重复。
3. 只能调用工具目录里的 ontology.* 原语、function.* 确定性函数、action.plan 或 tbox.describe，arguments 严格使用对应参数名；不要编造不存在的工具，也不要调用 action.execute。
4. 不要编造事实；工具返回 0 条时必须明确说明，不能假设存在。
5. 遇到 SCHEMA_ERROR 时读取 allowed values，修正类型/属性/操作符后重试，不要重复同一非法查询。若报错字段是 evidence_id 这类关联对象主键（不在当前类型的 allowed_properties 内），说明它属于另一个对象，不要继续拿它过滤当前类型；改用 ontology.search_around / ontology.traverse 沿关系取关联对象（费用项证据走 FeeItem → Evidence 的 fee_supported_by_evidence），或优先调用对应 function.* 确定性函数。
6. 最多执行 {self.max_steps} 次 MCP / 本体调用，之后必须输出 final。
"""

    # ------------------------------------------------------------------ 解析
    @staticmethod
    def _parse_action(raw: str) -> dict | None:
        text = (raw or "").strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        candidates = [text]
        decoder = json.JSONDecoder()
        for idx, char in enumerate(text):
            if char == "{":
                try:
                    obj, _ = decoder.raw_decode(text[idx:])
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict):
                    candidates.append(obj)
                    break
        for candidate in candidates:
            if isinstance(candidate, dict):
                obj = candidate
            else:
                try:
                    obj = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            if isinstance(obj, dict):
                action = str(obj.get("action") or "")
                if action in ("tool", "final"):
                    return obj
                # 宽松兜底：模型给了结论但动作名不是 final / 或干脆没给动作名，
                # 只要有 answer 就按 final 收下——宁可放过一次格式偏差，
                # 也不要因为字段名的小出入把整个诊断变成 503。
                if obj.get("answer") and action in ("", "conclude", "answer",
                                                   "conclusion", "结论", "final_answer"):
                    obj = dict(obj)
                    obj["action"] = "final"
                    obj.setdefault("title", "结论")
                    obj.setdefault("thought", "")
                    obj.setdefault("question", "")  # staticmethod 里没有实例上下文
                    return obj
        return None

    @staticmethod
    def _compact(value: Any, limit: int = OBSERVATION_LIMIT) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        if len(text) > limit:
            text = text[:limit] + "…(已截断)"
        return text

    def _normalize_object_id(self, value: Any) -> Any:
        if not isinstance(value, str) or not value or ":" in value:
            return value
        for prefix in ("waybill", "order", "fee", "settlement", "invoice",
                       "exception", "evidence", "driver", "vehicle", "shipper"):
            candidate = f"{prefix}:{value}"
            if self.facts._row(candidate):
                return candidate
        return value

    def _normalize_arguments(self, arguments: Any) -> dict:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        singular = {
            "object_id", "waybill_id", "order_id", "driver_id", "vehicle_id",
            "shipper_id", "fee_id", "settlement_id", "invoice_id",
            "exception_id", "subject_id",
        }
        normalized = {}
        for key, value in arguments.items():
            if isinstance(value, str):
                value = value.strip()
            if key in singular:
                value = self._normalize_object_id(value)
            if key == "object_ids" and isinstance(value, str):
                value = [value]
            if key == "object_ids" and isinstance(value, list):
                value = [self._normalize_object_id(str(item)) for item in value]
            if key == "limit":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = 100
            normalized[key] = value
        return normalized

    # ------------------------------------------------------------------ TBox 反查
    def _tbox_items(self, tool: str, result: Any,
                    arguments: dict | None = None,
                    scope: dict | None = None) -> tuple[list[dict], dict]:
        scoped = scope is not None
        if scoped:
            types = set(scope.get("types") or [])
            fee_types = set(scope.get("fee_types") or [])
            predicates: dict[str, int] = {}
        else:
            types, predicates, fee_types = self._collect_refs(result)
            tool_types = {
                "tms.get_waybill": {"Waybill"},
                "tms.get_order": {"Order"},
                "tms.get_driver": {"Driver"},
                "tms.get_vehicle": {"Vehicle"},
                "crm.get_shipper": {"Shipper"},
                "billing.list_fee_items": {"FeeItem"},
                "billing.get_fee_item": {"FeeItem"},
                "finance.get_settlement": {"Settlement"},
                "finance.get_invoice": {"Invoice"},
                "cs.list_exceptions": {"ExceptionTicket"},
                "evidence.list": {"Evidence"},
            }
            types |= tool_types.get(tool, set())
        described = self.sem.describe(sorted(types), sorted(fee_types))
        implied_relations = {
            "billing.list_fee_items": {"waybill_produces_fee"},
            "billing.get_fee_item": {"waybill_produces_fee"},
            "finance.get_settlement": {"fee_included_in_settlement"},
            "finance.get_invoice": {"settlement_generates_invoice"},
            "cs.list_exceptions": {"waybill_has_exception"},
        }.get(tool, set())
        if scoped:
            implied_relations = set()
        if tool == "evidence.list":
            object_ids = [str(i) for i in (arguments or {}).get("object_ids") or []]
            if any(i.startswith("fee:") for i in object_ids):
                implied_relations = {"fee_supported_by_evidence"}
            elif any(i.startswith("waybill:") for i in object_ids):
                implied_relations = {"waybill_supported_by_evidence"}
        items: list[dict] = []
        for cls in described["classes"]:
            props = cls.get("properties", [])
            if isinstance(props, dict):
                props = list(props)
            detail = (
                f'主键 {cls.get("key", "—")} · 属性 '
                + " / ".join(map(str, props[:6]))
            )
            if len(props) > 6:
                detail += f" 等 {len(props)} 项"
            items.append({
                "kind": "class", "id": cls["id"],
                "label": cls["label"], "detail": detail,
            })
        for rule in described["rules"]:
            judge = rule.get("judge") or {}
            detail = f'作用域 {rule.get("domain")} · '
            detail += judge.get("formula") or judge.get("explain") or "规则定义"
            items.append({
                "kind": "rule", "id": rule["id"],
                "label": rule.get("name") or rule["id"], "detail": detail[:140],
            })
        for action in described["actions"]:
            from_status = "/".join(action.get("from_status") or []) or "任意"
            detail = (
                f'{action["target"]} · {from_status} → {action.get("to_status") or "—"} · '
                f'角色 {"/".join(action.get("roles") or [])}'
            )
            items.append({
                "kind": "action", "id": action["id"],
                "label": action.get("label") or action["id"], "detail": detail,
            })
        for fee_type, pricing in described["pricing"].items():
            if fee_type == "waiting":
                # 知识型问答的答案就是这些参数（免费分钟数、计费块、单价），
                # 只给"决策表 7 行"这种摘要等于没给——必须把行渲染出来。
                matrix = pricing.get("matrix") or []
                dims = pricing.get("dimensions") or []
                rows = []
                for row in matrix[:8]:
                    conds = row.get("conditions") or {}
                    values = [
                        str(item.get("value"))
                        for grp in conds.values() if isinstance(grp, list)
                        for item in grp if isinstance(item, dict)
                    ]
                    cond = "+".join(values) or "默认"
                    params = "，".join(
                        f"{k}={v}" for k, v in (row.get("params") or {}).items()
                    )
                    rows.append(f"[{cond}] {params}")
                detail = (
                    f'维度 {" × ".join(dims) or "—"}：' + "；".join(rows)
                    if rows else json.dumps(pricing, ensure_ascii=False)
                )[:600]
                label = "等候费定价参数"
            elif fee_type == "highway":
                # 来源只能来自本体声明的决策表默认行；读不到就如实说没有，
                # 不用常量猜一个值充当「证据」。
                source = None
                for row in pricing.get("matrix") or []:
                    if not (row.get("conditions") or {}):
                        source = (row.get("params") or {}).get("source")
                detail = (f"来源 {source}" if source
                          else "来源未在本体声明中给出（不推断）")
                label = "高速费定价参数"
            else:
                detail = json.dumps(pricing, ensure_ascii=False)
                label = f"{fee_type} 定价参数"
            items.append({
                "kind": "pricing", "id": f"pricing.{fee_type}",
                "label": label, "detail": detail,
            })
        for rel in described["relations"]:
            hits = predicates.get(rel["id"], 0)
            implied = rel["id"] in implied_relations
            detail = f'{rel["domain"]} → {rel["range"]} · '
            if hits:
                detail += f"本步命中 {hits} 条"
            elif scoped:
                detail += "按业务问题圈定的邻接关系"
            elif implied:
                detail += "本步按此关系取数"
            else:
                detail += "类邻接关系（本步未直接使用）"
            items.append({
                "kind": "relation", "id": rel["id"],
                "label": rel["label"], "detail": detail,
            })
        self.types_seen.update(cls["id"] for cls in described["classes"])
        self.fee_types_seen.update(described["pricing"].keys())
        for item in items:
            self.tbox_seen[item["id"]] = item
        query_inputs = {
            "types": [cls["id"] for cls in described["classes"]],
            "fee_types": list(described["pricing"].keys()),
        }
        return items, query_inputs

    @staticmethod
    def _tbox_query(query_inputs: dict) -> str:
        payload = {}
        if query_inputs.get("types"):
            payload["types"] = query_inputs["types"]
        if query_inputs.get("fee_types"):
            payload["fee_types"] = query_inputs["fee_types"]
        text = f'tbox.describe({json.dumps(payload, ensure_ascii=False)})'
        if len(text) > QUERY_EXPR_LIMIT:
            text = text[:QUERY_EXPR_LIMIT] + "…（已截断）"
        return text

    @staticmethod
    def _tbox_context(items: list[dict], limit: int = TBOX_CONTEXT_LIMIT) -> str:
        """把圈定范围内的 TBox 定义压成带长度上限的上下文。

        截断时的优先级：类/规则/动作/函数/定价参数先保留，关系行用剩余
        预算装。规则公式与定价参数（如免费分钟数）是知识型问题的答案，
        不能被数量占优的邻接关系挤出上下文。
        """
        priority = {"class": 0, "rule": 1, "action": 2, "function": 3,
                    "pricing": 4, "relation": 5}
        ordered = sorted(
            items, key=lambda it: priority.get(it.get("kind"), 6)
        )
        lines = ["【TBox 定义（按本次问题按需加载，非全量）】"]
        kept: list[str] = []
        used = len(lines[0])
        for item in ordered:
            line = f'{item["kind"]}|{item["id"]}|{item["label"]}|{item["detail"]}'
            if used + len(line) + 1 > limit and kept:
                break
            kept.append(line)
            used += len(line) + 1
        text = "\n".join(lines + kept)
        if len(ordered) > len(kept):
            text += (
                f"\n…（{len(ordered) - len(kept)} 条关系行超出预算被省略；"
                "需要其他类型请调用 tbox.describe）"
            )
        return text

    @staticmethod
    def _tbox_query_explain(query_inputs: dict) -> str:
        types = query_inputs.get("types") or []
        fee_types = query_inputs.get("fee_types") or []
        subject = "、".join(types) if types else "本步对象"
        text = (
            f"大白话：查本体字典——{subject} 有哪些字段、"
            "连着哪些关系、受哪些规则约束"
        )
        if fee_types:
            text += f"；{'、'.join(fee_types)} 按什么价格算"
        return text + "。"

    @staticmethod
    def _tbox_returned_explain(items: list[dict]) -> str:
        if not items:
            return "大白话：本步没有新增定义，沿用前面查到的本体知识。"
        labels = {
            "class": "类", "relation": "关系", "rule": "规则",
            "action": "动作", "function": "函数", "pricing": "参数",
        }
        counts: dict[str, int] = {}
        for item in items:
            kind = item.get("kind")
            counts[kind] = counts.get(kind, 0) + 1
        parts = [
            f"{counts[kind]} 个{labels[kind]}"
            for kind in ("class", "relation", "rule", "action", "function", "pricing")
            if counts.get(kind)
        ]
        return (
            f"大白话：只拿到规则和定义，不含任何一票业务数据；"
            f"本步共 {len(items)} 项（{'、'.join(parts)}）。"
        )

    @staticmethod
    def _tbox_returned(items: list[dict]) -> str:
        if not items:
            return "没有返回新定义：本步对象和关系都能被前面步骤的 TBox 定义覆盖。"
        labels = {
            "class": "类", "relation": "关系", "rule": "规则",
            "action": "动作", "function": "函数", "pricing": "参数",
        }
        counts: dict[str, int] = {}
        for item in items:
            kind = item.get("kind")
            counts[kind] = counts.get(kind, 0) + 1
        parts = [
            f"{labels[kind]} {counts[kind]}"
            for kind in ("class", "relation", "rule", "action", "function", "pricing")
            if counts.get(kind)
        ]
        names = "、".join(f'{item["id"]} {item["label"]}' for item in items[:4])
        more = f" 等 {len(items)} 项" if len(items) > 4 else ""
        return (
            f'命中 {len(items)} 项（{" / ".join(parts)}）：{names}{more}。'
            "只返回定义，不含业务实例。"
        )

    def _abox_returned(self, tool: str, result: Any, rows: int,
                       error: str | None) -> str:
        if error:
            return f"查询失败：{error}"
        if not rows:
            return "返回 0 行：没有查到业务事实。"
        record = self._first_record(result)
        if not record:
            return f"返回 {rows} 行。"
        record = self._flatten_record(record)
        if tool == "billing.list_fee_items" and isinstance(result, list):
            anomalies = []
            for item in result:
                if not isinstance(item, dict):
                    continue
                fee_row = self._flatten_record(item)
                try:
                    expected = float(fee_row.get("expected_amount"))
                    actual = float(fee_row.get("actual_amount"))
                except (TypeError, ValueError):
                    continue
                if abs(actual - expected) > 0.005:
                    anomalies.append((fee_row, actual - expected))
            if anomalies:
                fee_row, delta = anomalies[0]
                return (
                    f'返回 {rows} 行：{len(anomalies)} 项与规则不符；'
                    f'{fee_row.get("fee_id")} {fee_row.get("fee_label")} '
                    f'规则应计 {float(fee_row.get("expected_amount")):.2f}'
                    f' / 实收 {float(fee_row.get("actual_amount")):.2f}'
                    f'（差 {delta:+.2f}）'
                )
        extra_labels = {
            "waybill_id": "运单号", "order_id": "订单号", "estimated_fee": "预估",
            "actual_fee": "实收", "variance": "差额", "waiting_minutes": "等候分钟",
            "ticket_id": "工单", "ticket_type": "工单类型", "reason": "原因",
            "resolution": "处理结果", "assignee": "处理人",
        }
        preferred = {
            "evidence.list": ["evidence_id", "evidence_type", "summary"],
            "finance.get_settlement": [
                "settlement_id", "status", "amount", "reversed_amount", "posted_at",
            ],
            "finance.get_invoice": ["invoice_id", "status", "amount", "invoice_no"],
            "cs.list_exceptions": [
                "ticket_id", "ticket_type", "status", "reason", "resolution",
            ],
            "tms.get_waybill": [
                "waybill_id", "status", "estimated_fee", "actual_fee", "variance",
            ],
            "billing.list_fee_items": [
                "fee_id", "fee_label", "expected_amount", "actual_amount", "status",
            ],
        }.get(tool, [
            "object_id", "name", "status", "label",
            "subject_name", "predicate", "object_name",
        ])
        pairs: list[str] = []
        for key in preferred:
            value = record.get(key)
            if value is None or value == "" or isinstance(value, (dict, list)):
                continue
            if isinstance(value, float):
                value = f"{value:.2f}"
            label = FIELD_LABELS.get(key) or extra_labels.get(key) or key
            pairs.append(f"{label}={value}")
            if len(pairs) >= 4:
                break
        if not pairs:
            return f"返回 {rows} 行。"
        return f'返回 {rows} 行：{" · ".join(pairs)}'

    @staticmethod
    def _abox_query_explain(tool: str) -> str:
        actions = {
            "ontology.schema": "读取本体 schema，并沿强依赖白名单展开闭包",
            "ontology.path": "查询两个类型之间的合法关系路径",
            "ontology.query": "按 schema 校验后的条件查询本体对象",
            "ontology.traverse": "沿关系多跳遍历实例子图",
            "ontology.aggregate": "按类型分组聚合并计算指标",
            "function.fee_breakdown": "用确定性函数拆解报价、应计、实收和差异",
            "function.quote_reconcile": "用确定性函数对账报价明细与实际费用",
            "function.evidence_chain": "用确定性函数汇总证据链和缺口",
            "action.plan": "用本体规则预检可用动作、阻断原因和影响面",
            "tms.get_waybill": "去 TMS 运单系统取这票运单的真实数据",
            "tms.get_order": "去 TMS 订单系统取订单的真实数据",
            "tms.get_driver": "去 TMS 司机系统取司机真实数据",
            "tms.get_vehicle": "去 TMS 车辆系统取车辆真实数据",
            "crm.get_shipper": "去 CRM 货主系统取货主真实数据",
            "billing.list_fee_items": "去计费中心取这票运单的全部费用项",
            "billing.get_fee_item": "去计费中心取指定费用项的真实数据",
            "finance.get_settlement": "去财务系统取这票运单的结算单",
            "finance.get_invoice": "去财务系统取对应的发票",
            "cs.list_exceptions": "去客服工单系统取这票运单的异常工单",
            "evidence.list": "去证据中心取和这个对象有关的原始证据",
            "graph.list_relations": "去图数据库按关系取关联对象的真实数据",
            "tms.list_events": "去 TMS 查这票运单的履约事件",
            "tms.list_objects": "去 TMS 按类型取对象列表",
            "audit.list": "去审计系统查这个对象的操作记录",
        }
        return f"大白话：{actions.get(tool, '去业务系统取本步需要的真实数据')}。"

    def _abox_returned_explain(self, tool: str, result: Any, rows: int,
                               error: str | None) -> str:
        if error:
            return f"大白话：没取到数据（{error}），Agent 会换工具或参数重试。"
        if not rows:
            return "大白话：业务系统里没有查到这票数据。"
        record = self._first_record(result)
        record = self._flatten_record(record) if record else {}
        status_map = {
            "DELIVERED": "已送达", "POSTED": "已入账", "ISSUED": "已开票",
            "OPEN": "待处理", "CLOSED": "已关闭",
        }
        status = status_map.get(str(record.get("status")), record.get("status"))

        def money(value: Any) -> str:
            try:
                return f"{float(value):.2f}"
            except (TypeError, ValueError):
                return str(value or "—")

        if tool == "tms.get_waybill":
            return (
                f"大白话：运单实际状态是{status or '未知'}，"
                f"预估 {money(record.get('estimated_fee'))}、"
                f"实收 {money(record.get('actual_fee'))}。"
            )
        if tool == "billing.list_fee_items":
            anomalies = 0
            if isinstance(result, list):
                for item in result:
                    if not isinstance(item, dict):
                        continue
                    fee_row = self._flatten_record(item)
                    try:
                        expected = float(fee_row.get("expected_amount"))
                        actual = float(fee_row.get("actual_amount"))
                    except (TypeError, ValueError):
                        continue
                    if abs(actual - expected) > 0.005:
                        anomalies += 1
            extra = f"，其中 {anomalies} 项和规则应计不一致" if anomalies else ""
            return f"大白话：这票运单实际有 {rows} 个费用项{extra}。"
        if tool == "cs.list_exceptions":
            return (
                f"大白话：客服系统里有一张工单（{record.get('ticket_type', '—')}），"
                f"状态 {status or '未知'}，说明客户已经在反馈问题。"
            )
        if tool == "finance.get_settlement":
            return (
                f"大白话：结算单状态是{status or '未知'}，金额 "
                f"{money(record.get('amount'))}；入账后不能直接改数，只能冲正。"
            )
        if tool == "finance.get_invoice":
            return (
                f"大白话：发票状态是{status or '未知'}，金额 "
                f"{money(record.get('amount'))}。"
            )
        if tool == "evidence.list":
            return (
                f"大白话：找到 {rows} 条原始证据（{record.get('evidence_type', '—')}），"
                "它用来支撑规则判断，而不是直接给结论。"
            )
        if tool == "graph.list_relations":
            return f"大白话：沿着这条关系实际找到 {rows} 个关联对象。"
        if tool == "tms.list_events":
            return f"大白话：实际查到 {rows} 条履约事件。"
        if tool == "tms.list_objects":
            return f"大白话：实际查到 {rows} 个业务对象。"
        if tool == "audit.list":
            return f"大白话：实际查到 {rows} 条操作审计记录。"
        if tool == "ontology.query":
            return f"大白话：按 schema 条件实际查到 {rows} 个本体对象。"
        if tool == "ontology.traverse":
            return f"大白话：沿关系实际遍历到 {rows} 个关联对象。"
        if tool == "ontology.aggregate":
            return f"大白话：分组聚合实际返回 {rows} 行指标。"
        return f"大白话：业务系统实际返回了 {rows} 行数据。"

    @staticmethod
    def _flatten_record(record: dict) -> dict:
        flat = dict(record)
        props = flat.get("properties")
        if isinstance(props, dict):
            for key, value in props.items():
                flat.setdefault(key, value)
        return flat

    @staticmethod
    def _first_record(result: Any) -> dict | None:
        if isinstance(result, dict):
            for key in ("objects", "rows", "items", "data", "result"):
                value = result.get(key)
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    return value[0]
            return result
        if isinstance(result, list):
            for item in result:
                if isinstance(item, dict):
                    return item
        return None

    def _collect_refs(self, result: Any) -> tuple[set, dict, set]:
        types: set[str] = set()
        predicates: dict[str, int] = {}
        fee_types: set[str] = set()

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                type_id = node.get("type") or node.get("subject_type") or node.get("object_type")
                if isinstance(type_id, str) and type_id in self.sem.classes:
                    types.add(type_id)
                predicate = node.get("predicate")
                if isinstance(predicate, str) and predicate in self.sem.relations:
                    predicates[predicate] = predicates.get(predicate, 0) + 1
                fee_type = node.get("fee_type")
                if isinstance(fee_type, str):
                    fee_types.add(fee_type)
                props = node.get("properties")
                if isinstance(props, dict):
                    fee_type = props.get("fee_type")
                    if isinstance(fee_type, str):
                        fee_types.add(fee_type)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(result)
        return types, predicates, fee_types

    # ------------------------------------------------------------------ ABox 统计
    @staticmethod
    def _row_count(result: Any) -> int:
        if result is None:
            return 0
        if isinstance(result, list):
            return len(result)
        return 1

    def _counts(self, result: Any) -> dict:
        objects: set[str] = set()
        relations = 0
        evidence = 0

        def walk(node: Any) -> None:
            nonlocal relations, evidence
            if isinstance(node, dict):
                if node.get("object_id") and node.get("type") in self.sem.classes:
                    objects.add(node["object_id"])
                if node.get("subject_id") and node.get("predicate") and node.get("object_id"):
                    relations += 1
                if node.get("evidence_id") and node.get("evidence_type"):
                    evidence += 1
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(result)
        return {"objects": len(objects), "relations": relations, "evidence": evidence}

    @staticmethod
    def _join_text(tbox_items: list[dict], counts: dict, error: str | None) -> str:
        if error:
            return "ABox 查询失败，本步没有可用于和 TBox 对齐的事实。"
        names = "、".join(f'{item["id"]} {item["label"]}' for item in tbox_items[:5])
        facts = (
            f'ABox 装入对象 {counts["objects"]} 个、关系 {counts["relations"]} 条、'
            f'证据 {counts["evidence"]} 条。'
        )
        if names:
            return f"TBox 命中 {names}；{facts}按类主键和关系定义在内存中对齐。"
        return f"本步沿用已有 TBox 定义；{facts}"

    # ------------------------------------------------------------------ 数据集 / 图
    def _dataset(self, tool: str, result: Any) -> dict:
        if isinstance(result, list):
            source_rows = result
        elif isinstance(result, dict):
            source_rows = [result]
        else:
            source_rows = []
        rows: list[dict] = []
        label_map: dict[str, str] = {}
        for item in source_rows[:8]:
            if not isinstance(item, dict):
                continue
            flat: dict[str, Any] = {}
            for key, value in item.items():
                if key in ("properties", "property_labels", "payload"):
                    continue
                if isinstance(value, (str, int, float, bool)) or value is None:
                    flat[key] = value
            props = item.get("properties")
            if isinstance(props, dict):
                for key, value in props.items():
                    if key not in flat and (
                        isinstance(value, (str, int, float, bool)) or value is None
                    ):
                        flat[key] = value
            labels = item.get("property_labels")
            if isinstance(labels, dict):
                label_map.update(labels)
            if flat:
                rows.append(flat)
        if not rows:
            return {"datasets": []}
        if tool == "graph.list_relations":
            preferred = [
                "subject_id", "subject_name", "predicate", "label",
                "object_id", "object_name",
            ]
        elif tool.startswith("evidence."):
            preferred = [
                "evidence_id", "evidence_type", "source_system",
                "occurred_at", "summary", "status",
            ]
        elif tool.startswith("finance.get_settlement"):
            preferred = [
                "object_id", "name", "status", "period",
                "amount", "reversed_amount", "posted_at",
            ]
        else:
            preferred = [
                "object_id", "type_label", "name", "status", "fee_type",
                "quoted_amount", "expected_amount", "actual_amount", "calc_basis",
            ]
        keys: list[str] = []
        for key in preferred:
            if any(key in row for row in rows):
                keys.append(key)
        for row in rows:
            for key in row:
                if len(keys) >= 7:
                    break
                if key not in keys:
                    keys.append(key)
            if len(keys) >= 7:
                break
        columns = [{
            "key": key,
            "label": FIELD_LABELS.get(key) or label_map.get(key) or key,
        } for key in keys]
        return {
            "datasets": [{
                "caption": f"{tool or 'MCP 工具'} 返回原始数据",
                "columns": columns,
                "rows": rows,
                "note": "查询返回 0 行" if not rows else "",
            }]
        }

    def _graph(self, result: Any) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def add(object_id: Any, type_id: Any, status: Any = None) -> None:
            if not isinstance(object_id, str) or not object_id or object_id in nodes:
                return
            cls = self.sem.classes.get(type_id or "", {})
            nodes[object_id] = {
                "id": object_id,
                "type_label": cls.get("label") or type_id or "对象",
                "status": status or "—",
            }

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("object_id") and node.get("type") in self.sem.classes:
                    add(node["object_id"], node["type"], node.get("status"))
                if node.get("subject_id") and node.get("predicate") and node.get("object_id"):
                    add(node["subject_id"], node.get("subject_type"))
                    add(node["object_id"], node.get("object_type"))
                    label = (
                        node.get("label")
                        or (self.sem.relations.get(node["predicate"]) or {}).get("label")
                        or node["predicate"]
                    )
                    edges.append({
                        "from": node["subject_id"],
                        "to": node["object_id"],
                        "label": label,
                    })
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(result)
        seen = set()
        unique_edges = []
        for edge in edges:
            key = (edge["from"], edge["label"], edge["to"])
            if key in seen:
                continue
            seen.add(key)
            unique_edges.append(edge)
        root = self.object_id if self.object_id in nodes else (
            next(iter(nodes), "") if nodes else ""
        )
        keep = set(list(nodes)[:12])
        if root:
            keep.add(root)
        return {
            "root": root,
            "nodes": [node for node_id, node in nodes.items() if node_id in keep],
            "edges": [
                edge for edge in unique_edges
                if edge["from"] in keep and edge["to"] in keep
            ][:20],
        }