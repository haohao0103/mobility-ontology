"""本体变异提议的独立裁决者（Pass D）承重测试（借鉴 ontosmith adjudication）。

契约：
- 提议者与裁决者分离：裁决者是独立角色，只审语义；
- 裁决合同针对目录四类变异定制；决策顺序 keep → retype → veto；
- 确定性不变量：retype 不得换变异类型、veto 必须带 retype_attempt；
- 裁决不可用 / 输出非法 → 降级人审兜底（不冒称否决，也不自动采纳）；
- veto 的 retype_attempt、declined 的 retype_attempt 都不得蒸发（入台账）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from freight.proposals import (  # noqa: E402
    ProposalLedger,
    VERDICT_ACCEPTED,
    VERDICT_DECLINED,
    VERDICT_REVIEW,
    adjudicate_proposal,
    normalize_llm_decision,
    review_proposal,
)
from freight.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def sem():
    return SemanticLayer(str(ROOT / "freight" / "semantic_layer.json"))


@pytest.fixture(scope="module")
def waybill_id(sem):
    # 语义层里第一个 class id 作为合法 target（动态取，不硬编码）
    for cid in sem.classes:
        if cid:
            return cid
    return "Waybill"


def _fake_chat(decision, **extra):
    def _chat(messages):
        return json.dumps({"decision": decision, "reason": "测试理由",
                           **extra}, ensure_ascii=False)
    return _chat


def _term_proposal(term="甩挂"):
    return {"mutation_type": "glossary_add_term",
            "payload": {"term": term, "type_ref": "Waybill"}}


def _alias_proposal(waybill_id, alias):
    return {"mutation_type": "class_add_alias",
            "payload": {"class_id": waybill_id, "alias": alias}}


# --- 裁决 keep -------------------------------------------------------------

def test_adjudicate_keep_preserves_verdict(sem):
    report = review_proposal(_term_proposal(), sem,
                             chat_fn=_fake_chat("keep"))
    assert report["adjudication"]["adjudicated"] is True
    assert report["adjudication"]["decision"] == "keep"
    assert report["verdict"] == VERDICT_ACCEPTED


# --- 裁决 veto -------------------------------------------------------------

def test_adjudicate_veto_with_retype_attempt(sem):
    attempt = {"mutation_type": "glossary_add_term",
               "payload": {"term": "甩挂运输", "type_ref": "Waybill"}}
    report = review_proposal(
        _term_proposal(), sem,
        chat_fn=_fake_chat("veto", retype_attempt=attempt))
    assert report["verdict"] == VERDICT_DECLINED
    assert "裁决否决" in report["decline_reason"]
    assert report["retype_attempt"] == attempt


def test_adjudicate_veto_without_retype_attempt_is_completed(sem):
    report = review_proposal(_term_proposal(), sem,
                             chat_fn=_fake_chat("veto"))
    assert report["verdict"] == VERDICT_DECLINED
    adj = report["adjudication"]
    assert adj["enforcement"] == "veto_without_retype_attempt"
    assert "unconstructible" in adj["retype_attempt"]


def test_unrecognised_decision_fails_closed(sem):
    report = review_proposal(_term_proposal(), sem,
                             chat_fn=_fake_chat("maybe"))
    assert report["verdict"] == VERDICT_DECLINED
    assert report["adjudication"]["enforcement"] == "unrecognised_decision"


# --- 裁决 retype -----------------------------------------------------------

def test_adjudicate_retype_valid_replaces_proposal(sem, waybill_id):
    original = _alias_proposal(waybill_id, "快件")
    fixed = _alias_proposal(waybill_id, "快件单")
    def _chat(messages):
        return json.dumps({"decision": "retype", "reason": "别名更标准",
                           "final_proposal": fixed}, ensure_ascii=False)
    report = review_proposal(original, sem, chat_fn=_chat)
    assert report["adjudicated_retyped"] is True
    assert report["verdict"] == VERDICT_ACCEPTED
    assert report["proposal"]["payload"]["alias"] == "快件单"


def test_adjudicate_retype_cannot_change_mutation_type(sem, waybill_id):
    changed = {"mutation_type": "class_add_alias",
               "payload": {"class_id": waybill_id, "alias": "快件"}}
    # 原提案是 glossary_add_term，retype 却改成 class_add_alias → 否决
    def _chat(messages):
        return json.dumps({"decision": "retype", "reason": "换类型",
                           "final_proposal": changed}, ensure_ascii=False)
    report = review_proposal(_term_proposal(), sem, chat_fn=_chat)
    assert report["verdict"] == VERDICT_DECLINED
    assert report["adjudication"]["enforcement"] == "invalid_retype"


def test_adjudicate_retype_invalid_payload_vetoes(sem, waybill_id):
    bad = _alias_proposal(waybill_id, "   ")  # 空别名，非法
    def _chat(messages):
        return json.dumps({"decision": "retype", "reason": "x",
                           "final_proposal": bad}, ensure_ascii=False)
    report = review_proposal(_alias_proposal(waybill_id, "快件"),
                             sem, chat_fn=_chat)
    assert report["verdict"] == VERDICT_DECLINED
    assert report["adjudication"]["enforcement"] == "invalid_retype"


# --- 裁决不可用 / 非法 -----------------------------------------------------

def test_adjudicate_unparseable_downgrades_to_human_review(sem):
    report = review_proposal(_term_proposal(), sem,
                             chat_fn=lambda messages: "这不是 JSON")
    assert report["adjudication"]["adjudicated"] is False
    assert report["verdict"] == VERDICT_REVIEW
    assert "降级人审" in report["adoption"]


def test_adjudicate_call_failure_downgrades(sem):
    def _boom(messages):
        raise RuntimeError("gateway down")
    report = review_proposal(_term_proposal(), sem, chat_fn=_boom)
    assert report["adjudication"]["adjudicated"] is False
    assert report["verdict"] == VERDICT_REVIEW


def test_no_chat_fn_is_pure_deterministic(sem):
    report = review_proposal(_term_proposal(), sem, chat_fn=None)
    assert "adjudication" not in report
    assert report["verdict"] == VERDICT_ACCEPTED


def test_adjudicate_proposal_no_chat_fn(sem):
    res = adjudicate_proposal(_term_proposal(), sem, chat_fn=None)
    assert res["adjudicated"] is False
    assert res["error"] == "no adjudicator available"


# --- declined 的 retype_attempt 归一化 -------------------------------------

def test_normalize_declined_retype_attempt():
    attempt = {"mutation_type": "class_add_alias",
               "payload": {"class_id": "Waybill", "alias": "运单"}}
    outcome = normalize_llm_decision(
        {"declined": True, "reason": "类型归属不明",
         "retype_attempt": attempt})
    assert outcome.status == "declined"
    assert outcome.retype_attempt == attempt


def test_normalize_declined_ignores_bad_retype():
    outcome = normalize_llm_decision(
        {"declined": True, "reason": "x", "retype_attempt": "不是对象"})
    assert outcome.retype_attempt is None


# --- 台账不蒸发 ------------------------------------------------------------

def test_ledger_records_retype_attempt_and_adjudication(sem, tmp_path):
    attempt = {"mutation_type": "glossary_add_term",
               "payload": {"term": "甩挂运输", "type_ref": "Waybill"}}
    report = review_proposal(
        _term_proposal(), sem,
        chat_fn=_fake_chat("veto", retype_attempt=attempt))
    ledger = ProposalLedger(str(tmp_path / "proposals.jsonl"))
    record = ledger.append(report, source="test")
    assert record["retype_attempt"] == attempt
    assert record["adjudication"]["decision"] == "veto"
    assert record["verdict"] == VERDICT_DECLINED
