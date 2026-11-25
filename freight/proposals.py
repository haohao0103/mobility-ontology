"""本体变异提议通道（governed proposal channel）。

借鉴来源（两个开源项目的治理机制，按本项目架构改写）：

- ontogeny（apache/hugegraph-ai）：``MUTATION_CATALOG`` 封闭目录——LLM 只能
  从目录里选变异，不存在"发明一种变更"；``validate_llm_decision`` 在模型
  回答**之后**校验 catalog/schema/类型系统，从不假设模型输出合法；
  ``ProposeOutcome`` 保留 ``decline_reason``，拒绝提议的分析"must not
  evaporate"；t0~t3 分级（blast radius 递增，auto-merge 只留给最轻的层）。
- auto-ontology（NVIDIA）：LLM 只提**术语候选**（glossary/alias 非结构层），
  结构性断言（类/关系/规则的新增删除）永远留给人——提示词边界在这里
  落地为目录分层：非结构层才有 t0 资格。
- 本项目自身：发布钉死（``payload_sha256`` pin，fail-closed）。提案**绝不**
  写 ``semantic_layer.json``：产出的是 after 草案 + 门禁结论 + 台账记录。
  采纳后由人修改 build 源（``tools/build_ontology_v4.py``）重新 build 出
  新 pin——提案通道绕不过发布，发布也绕不过 pin。

分级语义（tier）：

- t0 ``auto-merge 候选``：非结构层（glossary/alias）+ 门禁全绿。含义是
  "免内容人审"，**不是**免发布：采纳仍走 build 流程产出新 pin。
- t1 ``人审``：matrix_add_row（决策表加分支，影响计价分支覆盖）。
- t2 ``人审 + 灰度``：rule_adjust_param（计费语义变更，最重的爆炸半径）。
- t3 ``human-only``（删除类/关系/规则）：**不在目录里**。目录封闭即政策——
  LLM 想提删除会被 ``INVALID_MUTATION`` 拒绝并留档，这是设计而非疏漏。

decline 分析不得蒸发：declined 的提议连同 decline_reason 一起进台账——
负信息（为什么不改）与正信息（改什么）同等可查。
"""

from __future__ import annotations

import copy
import json
import os
import time
import uuid
from dataclasses import dataclass, field

from . import change_gate
from .semantic import SemanticLayer, ontology_pin_digest, validate_semantic

# ---------------------------------------------------------------------------
# 封闭变异目录（MUTATION_CATALOG）
# ---------------------------------------------------------------------------
# 目录之外的一切 mutation_type 都会被拒绝（INVALID_MUTATION）。
# 每项声明：tier、语义描述、payload 字段规格。
# 注意 tier 的赋值原则：只有"非结构层"（术语/别名，不改变类型/规则/计价
# 结构）才可能是 t0；结构层一律 t1 起步；动计费数字是最重的 t2。

MUTATION_CATALOG: dict[str, dict] = {
    "glossary_add_term": {
        "tier": "t0",
        "layer": "non-structural",
        "description": "术语表加词条（term + 指向已有类的 type_ref）。",
        "payload": {
            "term": "str 非空，不得与既有 term 重复",
            "type_ref": "str 必须是已声明的 class id",
            "synonyms": "list[str] 可选",
            "colloquial": "list[str] 可选",
        },
    },
    "class_add_alias": {
        "tier": "t0",
        "layer": "non-structural",
        "description": "既有类加别名（不新增类、不动结构）。",
        "payload": {
            "class_id": "str 必须是已声明的 class id",
            "alias": "str 非空，不得与该类既有 label/aliases 重复",
        },
    },
    "matrix_add_row": {
        "tier": "t1",
        "layer": "structural",
        "description": "给 pricing 某费用项的决策表加一行（新计价分档）。",
        "payload": {
            "fee_type": "str 必须是 pricing 段已有费用项",
            "row": "dict: {id, label, conditions, params}；conditions 的字段"
                   "必须在该表既有行的字段并集内（不允许引入新维度）",
        },
    },
    "rule_adjust_param": {
        "tier": "t2",
        "layer": "structural",
        "description": "调整既有决策表行的参数数值（计费语义变更）。",
        "payload": {
            "rule_id": "str 必须是 pricing 段引用的 rule id",
            "matrix_row": "str 目标行 id（default 表示默认行）",
            "param": "str 必须是该行 params 里已有的键（收紧/调整，"
                     "不允许新增参数语义）",
            "value": "number >= 0",
        },
    },
}

VERDICT_ACCEPTED = "ACCEPTED_FOR_BUILD"      # 门禁全绿，可直接采纳（t0 免人审）
VERDICT_REVIEW = "NEEDS_HUMAN_REVIEW"        # 门禁绿但属结构层，须人审
VERDICT_DECLINED = "DECLINED"                # 规则拒绝（如重复声明），留 decline_reason
VERDICT_REJECTED = "REJECTED"                # 会引入新错误/破坏性变更
VERDICT_INVALID = "INVALID"                  # 不在目录 / payload 不合法


@dataclass
class ProposeOutcome:
    """LLM（或人）提议输出的归一化结果。

    对齐 ontogeny ``ProposeOutcome``：status 三态，declined 时
    decline_reason 必须保留——拒绝的分析不得蒸发。
    """

    status: str                       # proposed | declined | invalid
    proposal: dict | None = None      # status=proposed 时非空
    analysis: str = ""                # 提议方/拒绝方的分析文本
    decline_reason: str = ""          # status=declined 时必填


def normalize_llm_decision(raw: object) -> ProposeOutcome:
    """把 LLM 原始输出归一化为 ``ProposeOutcome``。

    接受的形态（宽松入口）：
    - {"propose": {...提案...}, "analysis": "..."} 或 {"proposal": {...}}
    - {"declined": true, "reason": "..."} / {"decline_reason": "..."}
    其余一律 invalid——后置规则层的第一道：**从不假设模型输出合法**。
    """
    if not isinstance(raw, dict):
        return ProposeOutcome(status="invalid",
                               analysis="LLM 输出不是对象")
    propose = raw.get("propose") or raw.get("proposal")
    if isinstance(propose, dict):
        proposal = dict(propose)
        proposal.setdefault("mutation_type", raw.get("mutation_type"))
        proposal.setdefault("payload", raw.get("payload"))
        return ProposeOutcome(status="proposed", proposal=proposal,
                              analysis=str(raw.get("analysis") or ""))
    if raw.get("declined") or raw.get("decline_reason"):
        return ProposeOutcome(
            status="declined",
            analysis=str(raw.get("analysis") or ""),
            decline_reason=str(
                raw.get("decline_reason") or raw.get("reason") or "未给出理由"))
    return ProposeOutcome(status="invalid",
                          analysis="LLM 输出既无 propose 也无 declined")


# ---------------------------------------------------------------------------
# 后置规则层：validate_proposal
# ---------------------------------------------------------------------------

def _error(errors: list[dict], code: str, message: str,
           severity: str = "error") -> None:
    errors.append({"code": code, "severity": severity, "message": message})


def _load_target_class(semantic: SemanticLayer, class_id: str):
    cls = semantic.classes.get(class_id)
    return cls


def validate_proposal(proposal: dict, semantic: SemanticLayer) -> dict:
    """后置规则层：在提议产生**之后**校验目录/schema/值域，从不假设。

    返回 ``{"valid": bool, "errors": [...], "duplicate": bool,
    "target_exists": bool}``。``duplicate=True`` 表示目标已声明——
    这是 DECLINED（有价值的负信息）而非 INVALID。
    """
    errors: list[dict] = []
    if not isinstance(proposal, dict):
        _error(errors, "PROPOSAL_NOT_OBJECT", "提案必须是对象")
        return {"valid": not errors, "errors": errors,
                "duplicate": False, "target_exists": False}

    mtype = proposal.get("mutation_type")
    if mtype not in MUTATION_CATALOG:
        _error(errors, "INVALID_MUTATION",
               f"mutation_type={mtype!r} 不在封闭目录内；"
               f"目录成员：{sorted(MUTATION_CATALOG)}。"
               "删除类/关系/规则（t3）不在目录里，永远走人工直接改 build 源。")
        return {"valid": not errors, "errors": errors,
                "duplicate": False, "target_exists": False}

    payload = proposal.get("payload")
    if not isinstance(payload, dict):
        _error(errors, "PAYLOAD_MISSING", "payload 必须是对象")
        return {"valid": not errors, "errors": errors,
                "duplicate": False, "target_exists": False}

    raw = semantic.raw
    duplicate = False
    target_exists = True

    if mtype == "glossary_add_term":
        term = payload.get("term")
        if not isinstance(term, str) or not term.strip():
            _error(errors, "PAYLOAD_TYPE", "term 必须是非空字符串")
        else:
            known = {g.get("term") for g in semantic.glossary}
            if term in known:
                duplicate = True
                _error(errors, "PROPOSAL_ALREADY_DECLARED",
                       f"术语 {term!r} 已在 glossary 声明", severity="declined")
        type_ref = payload.get("type_ref")
        if not isinstance(type_ref, str) or type_ref not in semantic.classes:
            _error(errors, "TARGET_UNKNOWN",
                   f"type_ref={type_ref!r} 不是已声明的 class id")
            target_exists = False
        for key in ("synonyms", "colloquial"):
            value = payload.get(key)
            if value is not None and (
                    not isinstance(value, list)
                    or not all(isinstance(x, str) for x in value)):
                _error(errors, "PAYLOAD_TYPE", f"{key} 必须是字符串列表")

    elif mtype == "class_add_alias":
        class_id = payload.get("class_id")
        cls = _load_target_class(semantic, class_id) \
            if isinstance(class_id, str) else None
        if cls is None:
            _error(errors, "TARGET_UNKNOWN",
                   f"class_id={class_id!r} 不是已声明的 class id")
            target_exists = False
        alias = payload.get("alias")
        if not isinstance(alias, str) or not alias.strip():
            _error(errors, "PAYLOAD_TYPE", "alias 必须是非空字符串")
        elif cls is not None:
            existing = {cls.get("label")} | set(cls.get("aliases") or [])
            if alias in existing:
                duplicate = True
                _error(errors, "PROPOSAL_ALREADY_DECLARED",
                       f"别名 {alias!r} 已存在于该类", severity="declined")

    elif mtype == "matrix_add_row":
        fee_type = payload.get("fee_type")
        pricing_entry = (raw.get("pricing") or {}).get(fee_type) \
            if isinstance(fee_type, str) else None
        if not isinstance(pricing_entry, dict):
            _error(errors, "TARGET_UNKNOWN",
                   f"fee_type={fee_type!r} 不是 pricing 段已有费用项")
            target_exists = False
            pricing_entry = None
        row = payload.get("row")
        if not isinstance(row, dict):
            _error(errors, "PAYLOAD_TYPE", "row 必须是对象")
            row = None
        if row is not None:
            for key in ("id", "label"):
                if not isinstance(row.get(key), str) or not row.get(key, "").strip():
                    _error(errors, "PAYLOAD_TYPE", f"row.{key} 必须是非空字符串")
            if row.get("id") == "default":
                _error(errors, "PAYLOAD_VALUE",
                       "默认行 id=default 保留，新行不得占用")
            conditions = row.get("conditions")
            if not isinstance(conditions, dict):
                _error(errors, "PAYLOAD_TYPE", "row.conditions 必须是对象")
            elif not conditions:
                _error(errors, "MATRIX_DEFAULT_NOT_LAST",
                       "决策表必须有且只有一行默认行且位于最后；"
                       "conditions 为空的新行会破坏该约束")
            else:
                # 结构化条件形态：{"all": [{"field", "op", "value"}, ...]}
                cond_list = conditions.get("all")
                if not isinstance(cond_list, list) or not cond_list:
                    _error(errors, "PAYLOAD_TYPE",
                           'row.conditions 必须形如 {"all": [{"field": ..., '
                           '"op": "eq", "value": ...}]}')
                else:
                    new_fields: set[str] = set()
                    for cond in cond_list:
                        if not isinstance(cond, dict) or \
                                not isinstance(cond.get("field"), str):
                            _error(errors, "PAYLOAD_TYPE",
                                   "conditions.all 的每一项必须是含 field 的对象")
                        else:
                            new_fields.add(cond["field"])
                    if new_fields and pricing_entry is not None:
                        existing_fields: set[str] = set(
                            pricing_entry.get("dimensions") or [])
                        if not existing_fields:
                            # 兜底：无 dimensions 声明时从既有行提取
                            for old in pricing_entry.get("matrix") or []:
                                if isinstance(old, dict):
                                    for cond in \
                                            (old.get("conditions") or {}).get("all") or []:
                                        if isinstance(cond, dict) and \
                                                isinstance(cond.get("field"), str):
                                            existing_fields.add(cond["field"])
                        unknown = new_fields - existing_fields
                        if unknown:
                            _error(errors, "PAYLOAD_VALUE",
                                   f"conditions 引入了新维度字段 {sorted(unknown)}；"
                                   "加维度是另一种变更，不在本目录")
            params = row.get("params")
            if not isinstance(params, dict) or not params:
                _error(errors, "PAYLOAD_TYPE", "row.params 必须是非空对象")
            else:
                for key, value in params.items():
                    if not isinstance(value, (int, float)) or isinstance(value, bool) \
                            or value < 0:
                        _error(errors, "PAYLOAD_TYPE",
                               f"params.{key} 必须是非负数值")

    elif mtype == "rule_adjust_param":
        rule_id = payload.get("rule_id")
        matrix_row = payload.get("matrix_row", "default")
        param = payload.get("param")
        value = payload.get("value")
        pricing_entry = None
        if isinstance(rule_id, str):
            for fee_key, entry in (raw.get("pricing") or {}).items():
                if isinstance(entry, dict) and entry.get("rule_id") == rule_id:
                    pricing_entry = (fee_key, entry)
                    break
        if pricing_entry is None:
            _error(errors, "TARGET_UNKNOWN",
                   f"rule_id={rule_id!r} 不是 pricing 段引用的规则 id")
            target_exists = False
        else:
            target_row = None
            for old in pricing_entry[1].get("matrix") or []:
                if isinstance(old, dict) and old.get("id") == matrix_row:
                    target_row = old
                    break
            if target_row is None:
                _error(errors, "TARGET_UNKNOWN",
                       f"matrix_row={matrix_row!r} 不在该决策表内")
                target_exists = False
            elif not isinstance(param, str) or param not in (target_row.get("params") or {}):
                _error(errors, "TARGET_UNKNOWN",
                       f"param={param!r} 不是该行 params 里已有的键；"
                       "新增参数语义不在本目录")
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or value < 0:
                _error(errors, "PAYLOAD_TYPE", "value 必须是非负数值")

    blocking = [e for e in errors if e["severity"] == "error"]
    return {"valid": not blocking and not duplicate,
            "errors": errors, "duplicate": duplicate,
            "target_exists": target_exists}


# ---------------------------------------------------------------------------
# 应用草案：apply_to_raw
# ---------------------------------------------------------------------------

def apply_to_raw(proposal: dict, raw: dict) -> dict:
    """把提案应用到一个 raw 副本上，返回 after 草案（纯函数）。

    调用前必须已通过 ``validate_proposal``（否则行为未定义）。
    after 是**未发布物**：``meta.payload_sha256`` 保持旧值，任何人直接拿
    after 去加载都会被 pin 复核拦下（fail-closed，正确行为）。采纳动作
    永远 = 人改 build 源 + 重 build 出新 pin。
    """
    after = copy.deepcopy(raw)
    mtype = proposal.get("mutation_type")
    payload = proposal.get("payload") or {}

    if mtype == "glossary_add_term":
        entry = {"term": payload.get("term"),
                 "type_ref": payload.get("type_ref")}
        if payload.get("synonyms"):
            entry["synonyms"] = list(payload["synonyms"])
        if payload.get("colloquial"):
            entry["colloquial"] = list(payload["colloquial"])
        after.setdefault("glossary", []).append(entry)

    elif mtype == "class_add_alias":
        for cls in after.get("classes") or []:
            if cls.get("id") == payload.get("class_id"):
                cls.setdefault("aliases", []).append(payload.get("alias"))
                break

    elif mtype == "matrix_add_row":
        entry = after["pricing"][payload.get("fee_type")]
        matrix = entry.setdefault("matrix", [])
        row = copy.deepcopy(payload.get("row") or {})
        if matrix and not (matrix[-1].get("conditions") or {}):
            # 默认行必须在最后：新条件行插到默认行之前
            matrix.insert(len(matrix) - 1, row)
        else:
            matrix.append(row)

    elif mtype == "rule_adjust_param":
        for entry in (after.get("pricing") or {}).values():
            if isinstance(entry, dict) and \
                    entry.get("rule_id") == payload.get("rule_id"):
                for row in entry.get("matrix") or []:
                    if isinstance(row, dict) and \
                            row.get("id") == payload.get("matrix_row", "default"):
                        row.setdefault("params", {})[payload.get("param")] = \
                            payload.get("value")
                        return after
    return after


# ---------------------------------------------------------------------------
# 组合门禁：review_proposal
# ---------------------------------------------------------------------------

def _new_issues(before_raw: dict, after_raw: dict) -> list[dict]:
    """提案**引入**的新 issue = after 的 issues − before 的 issues。

    存量问题不算提案的错——门禁只裁决"这次变更有没有把本体改坏"。
    集合断言按 (code, path, message) 逐键比对（对齐审计双规则：禁止以
    计数吻合替代逐键清单）。
    """
    before_keys = {(i.get("code"), i.get("path"), i.get("message"))
                   for i in validate_semantic(before_raw)}
    return [i for i in validate_semantic(after_raw)
            if (i.get("code"), i.get("path"), i.get("message"))
            not in before_keys]


def review_proposal(proposal: dict, semantic: SemanticLayer) -> dict:
    """组合门禁：后置规则层 → after 草案 → 破坏性分类 → 新错误 diff → 分级。

    返回的 verdict 决定处置：
    - ACCEPTED_FOR_BUILD（t0 + 全绿）：可直接采纳，免内容人审；
    - NEEDS_HUMAN_REVIEW（t1/t2 + 全绿）：人审后采纳；
    - DECLINED / REJECTED / INVALID：不采纳，原因入台账（不蒸发）。
    """
    report: dict = {
        "proposal": proposal,
        "mutation_type": proposal.get("mutation_type")
        if isinstance(proposal, dict) else None,
    }

    check = validate_proposal(proposal, semantic)
    report["validation"] = check
    if not check["valid"]:
        report["verdict"] = VERDICT_DECLINED if check["duplicate"] \
            else VERDICT_INVALID
        report["decline_reason"] = "; ".join(
            f"[{e['code']}] {e['message']}" for e in check["errors"]) \
            or "未通过规则校验"
        return report

    mtype = proposal["mutation_type"]
    catalog = MUTATION_CATALOG[mtype]
    tier = catalog["tier"]

    after = apply_to_raw(proposal, semantic.raw)

    # 破坏性分类：复用 change_gate（提案目录本身只放非破坏变异，
    # breaking 非零属防御性后验——目录+门禁双保险）。
    gate = change_gate.review(semantic.raw, after)
    breaking = gate.get("summary", {}).get("breaking", 0)

    issues = _new_issues(semantic.raw, after)

    report["after_digest"] = ontology_pin_digest(after)
    report["breaking"] = breaking
    report["new_issues"] = issues

    if breaking > 0:
        report["verdict"] = VERDICT_REJECTED
        report["decline_reason"] = (
            f"提案会引入 {breaking} 处破坏性变更"
            f"（{gate['summary']}）；封闭目录只允许非破坏变异")
        return report
    if issues:
        report["verdict"] = VERDICT_REJECTED
        report["decline_reason"] = (
            "提案引入新的校验错误："
            + "; ".join(f"[{i['code']}] {i['message']}" for i in issues))
        return report

    report["tier"] = tier
    report["auto_merge_eligible"] = tier == "t0"
    report["verdict"] = VERDICT_ACCEPTED if tier == "t0" \
        else VERDICT_REVIEW
    report["adoption"] = (
        "采纳 = 修改 build 源 tools/build_ontology_v4.py 后重新 build，"
        "产出新 payload_sha256；本提案不写 semantic_layer.json"
        "（发布钉死，fail-closed）。"
        + ("t0 免内容人审，可直接录入 build 源。"
           if tier == "t0" else "须经人工评审后录入 build 源。"))
    return report


# ---------------------------------------------------------------------------
# 台账：ProposalLedger
# ---------------------------------------------------------------------------

DEFAULT_LEDGER_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "runs", "ontology_proposals.jsonl")


class ProposalLedger:
    """提案台账：提议、处置结论、decline 分析一并留痕。

    对齐"decline 分析不得蒸发"：DECLINED/REJECTED/INVALID 的记录同样
    必须包含 decline_reason 字段——负信息与正信息同等可查。
    落 ``runs/ontology_proposals.jsonl``（与检索历史同惯例：服务运行期
    数据，不入库、不部署）。
    """

    def __init__(self, path: str = DEFAULT_LEDGER_PATH):
        self.path = path

    def append(self, review: dict, source: str = "external") -> dict:
        record = {
            "id": uuid.uuid4().hex[:12],
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": source,
            "mutation_type": review.get("mutation_type"),
            "verdict": review.get("verdict"),
            "tier": review.get("tier"),
            "auto_merge_eligible": review.get("auto_merge_eligible", False),
            "breaking": review.get("breaking", 0),
            "after_digest": review.get("after_digest"),
            "proposal": review.get("proposal"),
            "decline_reason": review.get("decline_reason"),
            "errors": (review.get("validation") or {}).get("errors"),
            "new_issues": review.get("new_issues"),
        }
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def recent(self, limit: int = 20) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        out = []
        for ln in reversed(lines[-limit:]):
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
        return list(reversed(out))


def catalog_overview() -> dict:
    """目录总览（给 API/人看）：每种变异的 tier、层、payload 规格。"""
    return {
        "catalog": {name: {
            "tier": spec["tier"],
            "layer": spec["layer"],
            "description": spec["description"],
            "payload": spec["payload"],
        } for name, spec in MUTATION_CATALOG.items()},
        "governance": (
            "目录封闭：t3（删除类/关系/规则）不在目录里，LLM 提删除会被"
            "INVALID_MUTATION 拒绝并留档。t0=非结构层+门禁全绿（免内容"
            "人审，采纳仍走 build 重出新 pin）；t1/t2 人审；动计费数字"
            "最重（t2）。decline 分析必入台账（不蒸发）。"),
    }
