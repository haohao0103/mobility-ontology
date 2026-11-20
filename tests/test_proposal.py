"""P4-3 proposal 分支治理承重测试（create -> review -> merge 全链路 + 拦截路径）。"""
from __future__ import annotations

import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

proposal = importlib.import_module("tools.proposal")


@pytest.fixture()
def proposal_env(tmp_path, monkeypatch):
    """把 proposals/releases 目录指向临时目录，main JSON 备份/还原。"""
    enh_dir = os.path.join(ROOT, "enhancements")
    main_path = os.path.join(enh_dir, "ontology_extension.json")
    with open(main_path, encoding="utf-8") as f:
        backup = f.read()

    monkeypatch.setattr(proposal, "PROPOSALS", str(tmp_path / "proposals"))
    monkeypatch.setattr(proposal, "RELEASES", str(tmp_path / "releases"))
    yield {"main": main_path, "tmp": str(tmp_path)}
    with open(main_path, "w", encoding="utf-8") as f:
        f.write(backup)


class TestFullFlow:
    def test_create_review_merge(self, proposal_env):
        rc = proposal.cmd_create(type("A", (), {"title": "改货主名称文案"}))
        assert rc == 0
        pid = sorted(os.listdir(os.path.join(proposal_env["tmp"], "proposals")))[0]
        pdir = os.path.join(proposal_env["tmp"], "proposals", pid)

        # 修改分支：改一个属性的展示文案（合法变更，且非破坏性）。
        # 注意不能「凭空注册一个类里不存在的属性」——那是非法变更，交叉引用校验会拦，
        # 而且扩展层无权给基础本体的类加属性（加属性必须改基础本体）。
        branch_path = os.path.join(pdir, "branch.json")
        with open(branch_path, encoding="utf-8") as f:
            branch = json.load(f)
        for p in branch["property_types"]:
            if p["name"] == "shipper_name":
                p["label"] = "货主名称（校对版）"
        with open(branch_path, "w", encoding="utf-8") as f:
            json.dump(branch, f, ensure_ascii=False, indent=1)

        rc = proposal.cmd_review(type("A", (), {"proposal_id": pid}))
        assert rc == 0
        with open(os.path.join(pdir, "review.json"), encoding="utf-8") as f:
            review = json.load(f)
        assert review["verdict"] == "PASS"
        assert review["breaking"] is False, "改文案不该被判成破坏性变更"

        rc = proposal.cmd_merge(type("A", (), {"proposal_id": pid}))
        assert rc == 0
        # main 已更新 + release 快照生成
        with open(proposal_env["main"], encoding="utf-8") as f:
            merged = json.load(f)
        labels = {p["name"]: p.get("label") for p in merged["property_types"]}
        assert labels["shipper_name"] == "货主名称（校对版）"
        releases = os.listdir(os.path.join(proposal_env["tmp"], "releases"))
        assert len(releases) >= 1
        with open(os.path.join(proposal_env["tmp"], "releases", releases[0]),
                  encoding="utf-8") as f:
            rel = json.load(f)
        assert rel["proposal"] == pid
        assert rel["checksum"]
        assert any(c["change"] == "ADDED" for c in rel["change_list"])


class TestBlockingPaths:
    def test_merge_without_review_rejected(self, proposal_env):
        with open(proposal_env["main"], encoding="utf-8") as f:
            before = f.read()
        proposal.cmd_create(type("A", (), {"title": "未评审"}))
        pid = sorted(os.listdir(os.path.join(proposal_env["tmp"], "proposals")))[0]
        rc = proposal.cmd_merge(type("A", (), {"proposal_id": pid}))
        assert rc == 1
        # main 未被改动（字节级一致）
        with open(proposal_env["main"], encoding="utf-8") as f:
            assert f.read() == before

    def test_merge_failing_review_rejected(self, proposal_env):
        """承重：分支改坏（used_by 未知类）-> review FAIL -> merge 拒绝。"""
        proposal.cmd_create(type("A", (), {"title": "坏分支"}))
        pid = sorted(os.listdir(os.path.join(proposal_env["tmp"], "proposals")))[0]
        pdir = os.path.join(proposal_env["tmp"], "proposals", pid)
        branch_path = os.path.join(pdir, "branch.json")
        with open(branch_path, encoding="utf-8") as f:
            branch = json.load(f)
        branch["property_types"][0]["used_by"] = ["NoSuchClass"]
        with open(branch_path, "w", encoding="utf-8") as f:
            json.dump(branch, f, ensure_ascii=False)

        rc = proposal.cmd_review(type("A", (), {"proposal_id": pid}))
        assert rc == 2  # FAIL
        rc = proposal.cmd_merge(type("A", (), {"proposal_id": pid}))
        assert rc == 1  # REJECTED

    def test_merge_revalidates_after_review(self, proposal_env):
        """承重：评审通过后分支又被改坏 -> merge 时二次校验拦截。"""
        proposal.cmd_create(type("A", (), {"title": "评审后偷改"}))
        pid = sorted(os.listdir(os.path.join(proposal_env["tmp"], "proposals")))[0]
        pdir = os.path.join(proposal_env["tmp"], "proposals", pid)
        rc = proposal.cmd_review(type("A", (), {"proposal_id": pid}))
        assert rc == 0

        # 评审后偷改：引入未知标记
        branch_path = os.path.join(pdir, "branch.json")
        with open(branch_path, encoding="utf-8") as f:
            branch = json.load(f)
        branch["property_types"][0]["marking"] = "GHOST"
        with open(branch_path, "w", encoding="utf-8") as f:
            json.dump(branch, f, ensure_ascii=False)

        rc = proposal.cmd_merge(type("A", (), {"proposal_id": pid}))
        assert rc == 1
        with open(proposal_env["main"], encoding="utf-8") as f:
            assert json.load(f)["property_types"][0].get("marking") != "GHOST"
