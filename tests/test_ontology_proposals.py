"""本体变异提议通道承重测试（借鉴 ontogeny evolve 治理 + auto-ontology 术语边界）。

契约：
- 目录封闭：t3（删除）不在目录里，LLM 提删除必须被 INVALID_MUTATION 拒绝；
- 后置规则层：payload / target / 值域在提议产生**之后**校验，从不假设；
- 分级：非结构层（glossary/alias）t0 免内容人审；结构层 t1/t2 人审；
- 提案纯内存：after 是深拷贝草案，绝不写 semantic_layer.json（发布钉死）；
- decline 分析不得蒸发：DECLINED 的 decline_reason 必须保留。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.proposals import (  # noqa: E402
    DEFAULT_LEDGER_PATH,
    MUTATION_CATALOG,
    ProposalLedger,
    apply_to_raw,
    catalog_overview,
    normalize_llm_decision,
    review_proposal,
    validate_proposal,
)
from freight.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def sem():
    return SemanticLayer(
        str(ROOT / "freight" / "semantic_layer.json"))


def _error_codes(report: dict) -> set[str]:
    return {e["code"] for e in
            (report.get("validation") or {}).get("errors") or []}


# ---------------------------------------------------------------------------
# 目录封闭（ontogeny MUTATION_CATALOG）
# ---------------------------------------------------------------------------

def test_catalog_is_closed_to_deletions(sem):
    report = review_proposal(
        {"mutation_type": "delete_class", "payload": {"class_id": "Waybill"}},
        sem)
    assert report["verdict"] == "INVALID"
    assert "INVALID_MUTATION" in _error_codes(report)
    # 目录只开「新增」不开放「删除」：没有任何删除类变异在目录里
    assert not [m for m in MUTATION_CATALOG
                if m.startswith(("delete_", "remove_", "drop_"))]


def test_catalog_contains_exactly_seven_governed_mutations():
    assert set(MUTATION_CATALOG) == {
        "glossary_add_term", "class_add_alias",
        "matrix_add_row", "rule_adjust_param",
        "class_add", "relation_add", "group_add_type"}
    # 只有非结构层可能是 t0（auto-ontology 边界：LLM 只提术语/别名）
    for name, spec in MUTATION_CATALOG.items():
        if spec["tier"] == "t0":
            assert spec["layer"] == "non-structural"
    # 结构层新增一律 t3（全人审，AI 候选永不自动进生产）
    for name in ("class_add", "relation_add", "group_add_type"):
        assert MUTATION_CATALOG[name]["tier"] == "t3"
        assert MUTATION_CATALOG[name]["layer"] == "structural"


def test_catalog_overview_documents_governance():
    overview = catalog_overview()
    assert "t3" in overview["governance"]
    assert "不蒸发" in overview["governance"]


# ---------------------------------------------------------------------------
# LLM 决策归一（ontogeny ProposeOutcome：decline 分析不得蒸发）
# ---------------------------------------------------------------------------

def test_normalize_llm_decision_three_states():
    proposed = normalize_llm_decision(
        {"propose": {"mutation_type": "glossary_add_term",
                     "payload": {"term": "甩挂", "type_ref": "Waybill"}},
         "analysis": "高频口语"})
    assert proposed.status == "proposed"
    assert proposed.proposal["payload"]["term"] == "甩挂"

    declined = normalize_llm_decision(
        {"declined": True, "reason": "类型归属不明，建议人工先界定"})
    assert declined.status == "declined"
    assert declined.decline_reason  # 分析不得蒸发

    garbage = normalize_llm_decision("我觉得挺好的")
    assert garbage.status == "invalid"


# ---------------------------------------------------------------------------
# 后置规则层（validate_llm_decision：模型回答之后校验，从不假设）
# ---------------------------------------------------------------------------

def test_glossary_add_term_needs_known_type_ref(sem):
    report = validate_proposal(
        {"mutation_type": "glossary_add_term",
         "payload": {"term": "甩挂", "type_ref": "NoSuchClass"}}, sem)
    assert not report["valid"]
    assert "TARGET_UNKNOWN" in {e["code"] for e in report["errors"]}


def test_rule_adjust_param_rejects_unknown_param(sem):
    report = validate_proposal(
        {"mutation_type": "rule_adjust_param",
         "payload": {"rule_id": "R-WAIT-01", "matrix_row": "default",
                     "param": "surge_multiplier", "value": 1.2}}, sem)
    assert not report["valid"]
    assert "TARGET_UNKNOWN" in {e["code"] for e in report["errors"]}


def test_rule_adjust_param_rejects_negative_value(sem):
    report = validate_proposal(
        {"mutation_type": "rule_adjust_param",
         "payload": {"rule_id": "R-WAIT-01", "matrix_row": "default",
                     "param": "per_block", "value": -1}}, sem)
    assert not report["valid"]
    assert "PAYLOAD_TYPE" in {e["code"] for e in report["errors"]}


def test_matrix_add_row_rejects_new_dimension(sem):
    report = validate_proposal(
        {"mutation_type": "matrix_add_row",
         "payload": {"fee_type": "waiting",
                     "row": {"id": "moon", "label": "月相加价",
                             "conditions": {"all": [
                                 {"field": "moon_phase", "op": "eq",
                                  "value": "full"}]},
                             "params": {"per_block": 9.9}}}}, sem)
    assert not report["valid"]
    assert "PAYLOAD_VALUE" in {e["code"] for e in report["errors"]}


# ---------------------------------------------------------------------------
# 组合门禁：分级 + 破坏性 + 新错误 diff + 纯内存
# ---------------------------------------------------------------------------

def test_glossary_add_term_accepted_for_build_t0(sem):
    before_count = len(sem.glossary)
    report = review_proposal(
        {"mutation_type": "glossary_add_term",
         "payload": {"term": "甩挂", "type_ref": "Waybill",
                     "synonyms": ["甩挂运输"]}}, sem)
    assert report["verdict"] == "ACCEPTED_FOR_BUILD"
    assert report["tier"] == "t0"
    assert report["auto_merge_eligible"] is True
    assert report["breaking"] == 0
    assert report["new_issues"] == []
    assert report["after_digest"]
    assert "build" in report["adoption"]
    assert len(sem.glossary) == before_count  # 不改内存层对象


def test_duplicate_term_declined_with_reason(sem):
    known = {g.get("term") for g in sem.glossary}
    term = sorted(known)[0]
    report = review_proposal(
        {"mutation_type": "glossary_add_term",
         "payload": {"term": term, "type_ref": "Waybill"}}, sem)
    assert report["verdict"] == "DECLINED"
    assert "PROPOSAL_ALREADY_DECLARED" in _error_codes(report)
    assert report["decline_reason"]  # 拒绝分析留档，不蒸发


def test_class_add_alias_t0(sem):
    report = review_proposal(
        {"mutation_type": "class_add_alias",
         "payload": {"class_id": "Waybill", "alias": "货运单据"}}, sem)
    assert report["verdict"] == "ACCEPTED_FOR_BUILD"
    assert report["auto_merge_eligible"] is True


def test_matrix_add_row_needs_human_review(sem):
    report = review_proposal(
        {"mutation_type": "matrix_add_row",
         "payload": {"fee_type": "waiting",
                     "row": {"id": "vehicle:冷藏车", "label": "冷藏车",
                             "conditions": {"all": [
                                 {"field": "vehicle_type", "op": "eq",
                                  "value": "冷藏车"}]},
                             "params": {"free_minutes": 25,
                                        "block_minutes": 5,
                                        "per_block": 3.0}}}}, sem)
    assert report["verdict"] == "NEEDS_HUMAN_REVIEW"
    assert report["tier"] == "t1"
    assert report["auto_merge_eligible"] is False


def test_rule_adjust_param_t2(sem):
    report = review_proposal(
        {"mutation_type": "rule_adjust_param",
         "payload": {"rule_id": "R-WAIT-01", "matrix_row": "default",
                     "param": "per_block", "value": 2.5}}, sem)
    assert report["verdict"] == "NEEDS_HUMAN_REVIEW"
    assert report["tier"] == "t2"


def test_apply_to_raw_is_pure_deepcopy(sem):
    before = json.dumps(sem.raw["pricing"]["waiting"], ensure_ascii=False)
    after = apply_to_raw(
        {"mutation_type": "rule_adjust_param",
         "payload": {"rule_id": "R-WAIT-01", "matrix_row": "default",
                     "param": "per_block", "value": 9.9}}, sem.raw)
    assert after["pricing"]["waiting"] is not sem.raw["pricing"]["waiting"]
    assert json.dumps(sem.raw["pricing"]["waiting"],
                      ensure_ascii=False) == before
    row = [r for r in after["pricing"]["waiting"]["matrix"]
           if r["id"] == "default"][0]
    assert row["params"]["per_block"] == 9.9


def test_matrix_add_row_inserts_before_default_row(sem):
    after = apply_to_raw(
        {"mutation_type": "matrix_add_row",
         "payload": {"fee_type": "waiting",
                     "row": {"id": "vehicle:冷藏车", "label": "冷藏车",
                             "conditions": {"all": [
                                 {"field": "vehicle_type", "op": "eq",
                                  "value": "冷藏车"}]},
                             "params": {"per_block": 3.0}}}}, sem.raw)
    matrix = after["pricing"]["waiting"]["matrix"]
    assert matrix[-1]["id"] == "default"  # 默认行必须保持在最后
    assert any(r["id"] == "vehicle:冷藏车" for r in matrix)


def test_proposal_never_writes_published_file(sem):
    path = ROOT / "freight" / "semantic_layer.json"
    before_bytes = path.read_bytes()
    review_proposal(
        {"mutation_type": "glossary_add_term",
         "payload": {"term": "甩挂", "type_ref": "Waybill"}}, sem)
    assert path.read_bytes() == before_bytes  # 发布物分毫不动


# ---------------------------------------------------------------------------
# 台账（decline 分析不得蒸发）
# ---------------------------------------------------------------------------

def test_ledger_roundtrip(tmp_path):
    ledger = ProposalLedger(str(tmp_path / "proposals.jsonl"))
    accepted = ledger.append({
        "mutation_type": "glossary_add_term",
        "verdict": "ACCEPTED_FOR_BUILD", "tier": "t0",
        "auto_merge_eligible": True, "breaking": 0,
        "proposal": {"mutation_type": "glossary_add_term",
                     "payload": {"term": "甩挂", "type_ref": "Waybill"}},
    }, source="test")
    declined = ledger.append({
        "mutation_type": "glossary_add_term",
        "verdict": "DECLINED",
        "decline_reason": "[PROPOSAL_ALREADY_DECLARED] 术语 运单 已声明",
        "proposal": {"mutation_type": "glossary_add_term",
                     "payload": {"term": "运单", "type_ref": "Waybill"}},
    }, source="test")
    records = ledger.recent()
    assert len(records) == 2
    assert records[0]["id"] == accepted["id"]
    assert records[1]["id"] == declined["id"]
    # 负信息与正信息同等可查
    declined_record = [r for r in records
                       if r["verdict"] == "DECLINED"][0]
    assert declined_record["decline_reason"]
    assert records[0]["id"] != records[1]["id"]


def test_default_ledger_path_points_at_runs():
    assert DEFAULT_LEDGER_PATH.endswith("runs/ontology_proposals.jsonl")
    assert "semantic_layer.json" not in DEFAULT_LEDGER_PATH
