"""本体发布钉死（payload_sha256 pin）——借鉴 OntoSmith packs 模式。

契约：
1. build 渲染时把内容摘要写进 meta.payload_sha256（排除 pin 字段自身）；
2. 磁盘加载逐字节复核，不符 **fail-closed** raise ONTOLOGY_PIN_MISMATCH——
   「改了内容却没重新发布」不可能静默发生；
3. 无 pin 的历史发布物（releases/*）跳过校验但如实标注 unpinned；
4. from_raw（demo lab 内存实验）只记录不抛——实验沙箱不是发布通道。
"""
import json
import os

import pytest

from freight.semantic import (
    ONTOLOGY_PIN_FIELD,
    BusinessReject,
    SemanticLayer,
    ontology_pin_digest,
    verify_ontology_pin,
)


def test_shipped_ontology_is_pinned_and_verifies():
    """仓 freight/semantic_layer.json 必须带 pin 且复核通过。"""
    sem = SemanticLayer()
    pin = (sem.raw.get("meta") or {}).get(ONTOLOGY_PIN_FIELD)
    assert pin, "发布物必须带 payload_sha256"
    assert sem.pin_verified is True
    assert sem.pin_detail == "verified"


def test_tampered_content_is_rejected_on_load(tmp_path):
    """改一个字节（不更新 pin）→ 磁盘加载即拒，fail-closed。"""
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    raw["classes"][0]["label"] = "被偷偷改过的标签"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    with pytest.raises(BusinessReject) as exc:
        SemanticLayer(str(path))
    assert exc.value.code == "ONTOLOGY_PIN_MISMATCH"
    assert "重新发布" in str(exc.value)


def test_updated_pin_makes_tampered_content_load(tmp_path):
    """改了内容也更新 pin = 正式发布，加载放行（pin 是发布闸口不是内容审查）。"""
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    raw["classes"][0]["label"] = "新版本标签"
    meta = raw["meta"]
    meta.pop(ONTOLOGY_PIN_FIELD, None)
    raw["meta"] = meta
    meta[ONTOLOGY_PIN_FIELD] = ontology_pin_digest(raw)
    raw["meta"] = meta
    path = tmp_path / "released.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    sem = SemanticLayer(str(path))
    assert sem.pin_verified
    assert sem.classes[raw["classes"][0]["id"]]["label"] == "新版本标签"


def test_unpinned_legacy_loads_with_marker(tmp_path):
    """无 pin 的历史发布物：跳过校验，pin_verified=True 但 detail=unpinned。"""
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    raw["meta"].pop(ONTOLOGY_PIN_FIELD, None)
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    sem = SemanticLayer(str(path))
    assert sem.pin_verified is True
    assert sem.pin_detail == "unpinned"


def test_from_raw_records_mismatch_without_raising():
    """from_raw（demo lab）只记录不抛：实验沙箱不是发布通道。"""
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    raw["classes"][0]["label"] = "lab 改动"
    sem = SemanticLayer.from_raw(raw)
    assert sem.pin_verified is False
    assert "actual=sha256:" in sem.pin_detail
    # 内容照常可用（lab 就是要改着玩）
    assert sem.classes


def test_pin_excludes_itself():
    """pin 字段自身不参与摘要——否则写入 pin 会改变被 pin 的内容。"""
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    before = ontology_pin_digest(raw)
    raw["meta"][ONTOLOGY_PIN_FIELD] = "0" * 64
    assert ontology_pin_digest(raw) == before


def test_verify_reports_actual_digest_on_mismatch():
    with open("freight/semantic_layer.json", encoding="utf-8") as f:
        raw = json.load(f)
    raw["meta"][ONTOLOGY_PIN_FIELD] = "f" * 64
    ok, detail = verify_ontology_pin(raw)
    assert ok is False
    assert detail.startswith("pin=")
    assert "actual=sha256:" in detail


def test_build_script_recomputes_pin_end_to_end(tmp_path):
    """build 脚本重跑后，产物 pin 必须自洽（防 build 与加载两侧漂移）。"""
    import subprocess
    import sys

    rc = subprocess.run(
        [sys.executable, "tools/build_ontology_v4.py"],
        capture_output=True, text=True, cwd=os.getcwd())
    assert rc.returncode == 0, rc.stderr
    sem = SemanticLayer()
    assert sem.pin_verified is True
    assert sem.raw["meta"]["version"].startswith("4.")
