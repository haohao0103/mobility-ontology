"""决策表计价（pricing matrix）回归测试。

背景：定价参数从「实例副本 + by_vehicle_type」迁到本体 pricing 决策表
（matrix 唯一源，首行命中）。本文件钉死三件事：
1. 零漂移——既有运单应计金额、费用诊断结论与迁移前逐值一致；
2. 多维命中——车型 × 天气 × 时段组合按行序首行命中，翻转可复现；
3. 旧版回放——无 matrix 的历史发布快照仍按 by_vehicle_type 口径求值，
   历史结论不因引擎升级而漂移。
"""
from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path

import pytest

from freight.engine import FreightSystem
from freight.semantic import SemanticLayer
from freight import demo_labs as dl

ROOT = Path(__file__).resolve().parents[1]
WB = "waybill:WB20261105001"


@pytest.fixture(scope="module")
def system():
    s = FreightSystem(str(Path(tempfile.mkdtemp()) / "f.db"),
                      fresh=True, seed=True, with_scenario=True)
    return s


def _waiting(sem: SemanticLayer, vehicle_type: str, weather: str,
             minutes: float, at: str) -> dict:
    """走与生产同一入口求值等候费，返回命中行与应计。"""
    ctx = sem._fee_context(
        "waiting", {},
        {"waiting_minutes": minutes, "weather": weather,
         "accepted_at": at}, vehicle_type)
    ast_node = (sem.rules.get(str((sem.pricing.get("waiting") or {})
                                  .get("rule_id")) or {}) or {}).get(
        "judge", {}).get("ast")
    from freight.semantic import evaluate_rule_ast
    value = evaluate_rule_ast(ast_node, ctx) if ast_node else None
    return {"row": ctx["tariff_row"], "tariff": ctx["tariff"],
            "expected": round(float(value), 2) if value is not None else None}


class TestZeroDrift:
    """迁移不得改变任何既有业务结论（对照迁移前 HEAD 实测值）。"""

    def test_waiting_fee_unchanged(self, system):
        r = system.fee_analysis(WB)
        finding = [f for f in r["findings"]
                   if f["code"] == "WAITING_FEE_MISMATCH"]
        assert len(finding) == 1
        assert finding[0]["expected"] == 12.0      # 53 分钟 / 免费 30 / 每 5 分钟 2.4
        assert finding[0]["actual"] == 25.0
        assert finding[0]["delta"] == 13.0
        assert r["variance"] == 38.0

    def test_no_false_params_missing(self, system):
        """实例不再携带参数副本（唯一源是 matrix），不得再报缺参。"""
        codes = [f["code"] for f in system.fee_analysis(WB)["findings"]]
        assert "PRICING_RULE_PARAMS_MISSING" not in codes
        assert codes == ["WAITING_FEE_MISMATCH"]

    def test_vehicle_tariff_contrast_unchanged(self, system):
        rows = system.fee_analysis(WB)["waiting_rule"]["tariffs"]
        by_vt = {r["vehicle_type"]: r for r in rows}
        assert set(by_vt) == {"4.2米厢货", "面包车", "小货车", "大货车", "跑腿"}
        assert by_vt["4.2米厢货"]["expected"] == 12.0
        assert by_vt["4.2米厢货"]["selected"] is True
        assert by_vt["大货车"]["expected"] == 8.0
        assert by_vt["面包车"]["expected"] == 12.6

    def test_instances_no_longer_carry_params(self, system):
        """seed 的定价规则实例只存版本元信息——副本即双口径。"""
        for rid in ("PR-BASE-01", "PR-WAIT-01", "PR-MILEAGE-01"):
            row = system.facts.get_row(f"pricing_rule:{rid}")
            props = json.loads(row["props"]) if isinstance(
                row["props"], str) else row["props"]
            assert not props.get("params"), f"{rid} 仍携带参数副本"


class TestMatrixFirstHit:
    """决策表首行命中：具体行在前，默认行兜底。"""

    def test_truck_rain_row_wins_over_vehicle_row(self, system):
        sem = system.sem
        rain = _waiting(sem, "大货车", "rain", 53, "2026-11-06T08:00:00+08:00")
        assert rain["row"] == "truck-rain"
        assert rain["tariff"]["per_block"] == 4.8
        assert rain["expected"] == 9.6            # ceil(13/10)*4.8
        normal = _waiting(sem, "大货车", "normal", 53,
                          "2026-11-06T08:00:00+08:00")
        assert normal["row"] == "vehicle:大货车"
        assert normal["expected"] == 8.0          # ceil(13/10)*4.0

    def test_default_row_fallback_for_unknown_vehicle(self, system):
        sem = system.sem
        unknown = _waiting(sem, "冷藏车", "normal", 53,
                           "2026-11-06T08:00:00+08:00")
        assert unknown["row"] == "default"
        assert unknown["tariff"]["per_block"] == 2.4
        assert unknown["expected"] == 12.0

    def test_row_order_specific_before_general(self):
        """门禁同源断言：具体行（truck-rain）必须排在通用车型行之前。"""
        layer = json.loads((ROOT / "freight" / "semantic_layer.json")
                           .read_text(encoding="utf-8"))
        matrix = layer["pricing"]["waiting"]["matrix"]
        ids = [r["id"] for r in matrix]
        assert ids[0] == "truck-rain"
        assert ids[-1] == "default"


class TestTimeWindow:
    """时段维度：按 time_windows 声明归窗，与首行命中正交。"""

    def test_peak_window_weekday_morning(self, system):
        sem = system.sem
        cfg = sem.pricing["waiting"]
        at = "2026-11-06T08:00:00+08:00"     # 周五早高峰
        assert sem._resolve_time_window(cfg, {"accepted_at": at}) == "peak"

    def test_weekend_morning_is_not_peak(self, system):
        sem = system.sem
        cfg = sem.pricing["waiting"]
        at = "2026-11-07T08:00:00+08:00"     # 周六同一时刻
        assert sem._resolve_time_window(cfg, {"accepted_at": at}) == "normal"

    def test_night_window(self, system):
        sem = system.sem
        cfg = sem.pricing["waiting"]
        assert sem._resolve_time_window(
            cfg, {"accepted_at": "2026-11-06T23:30:00+08:00"}) == "night"
        assert sem._resolve_time_window(
            cfg, {"accepted_at": "2026-11-06T05:30:00+08:00"}) == "night"

    def test_unparseable_time_falls_back_to_default(self, system):
        sem = system.sem
        cfg = sem.pricing["waiting"]
        assert sem._resolve_time_window(
            cfg, {"accepted_at": "not-a-time"}) == "normal"


class TestLegacyReplay:
    """无 matrix 的历史发布快照：by_vehicle_type 口径逐值保留。"""

    def test_release_snapshot_vehicle_override(self):
        release = ROOT / "freight" / "releases" / "1.3.1-demo" / \
            "semantic_layer.json"
        if not release.exists():
            pytest.skip("发布快照不存在")
        sem = SemanticLayer(str(release))
        cfg = sem.pricing["waiting"]
        assert "matrix" not in cfg and "by_vehicle_type" in cfg
        # 快照自有值即期望值：1.3.1 发布时 4.2米厢货 per_block=3.0，
        # 与当前工作副本的 2.4 不同——回放必须用当时值，不能用今天的口径
        legacy = cfg["by_vehicle_type"]
        for vt, override in legacy.items():
            tariff, row = sem._resolve_tariff(
                cfg, {"vehicle_type": vt, "waiting_minutes": 53})
            for key, value in override.items():
                assert tariff[key] == value, f"{vt}.{key} 回放漂移"
            assert row == "default"


class TestPricingLab:
    """多维计价实验室：只读求值、零写库、翻转可复现。"""

    def test_truck_rain_case(self, system):
        r = dl.pricing_lab(system, case="truck_rain", waiting_minutes=53)
        assert r["before"]["matched_row"] == "vehicle:大货车"
        assert r["after"]["matched_row"] == "truck-rain"
        assert r["before"]["expected"] == 8.0
        assert r["after"]["expected"] == 9.6
        codes = [f["code"] for f in r["flips"]]
        assert "TARIFF_ROW_FLIP" in codes and "EXPECTED_AMOUNT_FLIP" in codes
        assert r["persisted"] is False
        assert "matrix" in r["declaration_paths"][0]

    def test_vehicle_case(self, system):
        r = dl.pricing_lab(system, case="vehicle", waiting_minutes=53)
        assert r["dimension"] == "vehicle_type"
        assert r["before"]["expected"] == 12.0
        assert r["after"]["expected"] == 8.0
        assert any(f["code"] == "TARIFF_ROW_FLIP" for f in r["flips"])

    def test_lab_zero_pollution(self, system):
        c = system.conn
        before = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("objects", "relations", "evidence", "audit")}
        dl.pricing_lab(system, case="truck_rain")
        dl.pricing_lab(system, case="vehicle")
        after = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("objects", "relations", "evidence", "audit")}
        assert before == after


class TestGateRowOrder:
    """门禁负向用例：行序错误必须被拦住（首行命中会静默吞掉具体行）。"""

    @staticmethod
    def _gate_with_matrix(matrix: list) -> dict:
        from scripts.semantic_gate import check_pricing_matrix
        tmp = Path(tempfile.mkdtemp())
        try:
            layer = json.loads((ROOT / "freight" / "semantic_layer.json")
                               .read_text(encoding="utf-8"))
            layer["pricing"]["waiting"]["matrix"] = matrix
            (tmp / "freight").mkdir()
            (tmp / "freight" / "semantic_layer.json").write_text(
                json.dumps(layer, ensure_ascii=False), encoding="utf-8")
            return check_pricing_matrix(str(tmp))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_default_not_last_is_rejected(self):
        matrix = [
            {"id": "default", "conditions": {}, "params": {"per_block": 2.4}},
            {"id": "vehicle:大货车",
             "conditions": {"all": [{"field": "vehicle_type",
                                     "op": "eq", "value": "大货车"}]},
             "params": {"per_block": 4.0}},
        ]
        item = self._gate_with_matrix(matrix)
        assert item["ok"] is False
        assert any(i["code"] == "MATRIX_DEFAULT_NOT_LAST"
                   for i in item["issues"])

    def test_specific_row_after_general_is_rejected(self):
        matrix = [
            {"id": "vehicle:大货车",
             "conditions": {"all": [{"field": "vehicle_type",
                                     "op": "eq", "value": "大货车"}]},
             "params": {"per_block": 4.0}},
            {"id": "truck-rain",
             "conditions": {"all": [
                 {"field": "vehicle_type", "op": "eq", "value": "大货车"},
                 {"field": "weather", "op": "eq", "value": "rain"}]},
             "params": {"per_block": 4.8}},
        ]
        item = self._gate_with_matrix(matrix)
        assert item["ok"] is False
        assert any(i["code"] == "MATRIX_ROW_ORDER" for i in item["issues"])

    def test_current_layer_passes(self):
        from scripts.semantic_gate import check_pricing_matrix
        item = check_pricing_matrix(str(ROOT))
        assert item["ok"], item["issues"][:3]

    def test_dead_top_level_param_key_is_rejected(self):
        """有 matrix 还把参数写在顶层 = 死键（第二口径），门禁必须拦住。"""
        matrix = [
            {"id": "vehicle:大货车",
             "conditions": {"all": [{"field": "vehicle_type",
                                     "op": "eq", "value": "大货车"}]},
             "params": {"per_block": 4.0}},
            {"id": "default", "conditions": {}, "params": {"per_block": 2.4}},
        ]
        item = self._gate_with_matrix(matrix)
        # 当前层没有死键，先确认基线
        assert item["ok"], item["issues"][:3]
        # 再造一个带死键的层：顶层 per_block 与 matrix 并存
        tmp = Path(tempfile.mkdtemp())
        try:
            layer = json.loads((ROOT / "freight" / "semantic_layer.json")
                               .read_text(encoding="utf-8"))
            layer["pricing"]["waiting"]["matrix"] = matrix
            layer["pricing"]["waiting"]["per_block"] = 2.4   # 死键
            (tmp / "freight").mkdir()
            (tmp / "freight" / "semantic_layer.json").write_text(
                json.dumps(layer, ensure_ascii=False), encoding="utf-8")
            from scripts.semantic_gate import check_pricing_matrix
            bad = check_pricing_matrix(str(tmp))
            assert bad["ok"] is False
            assert any(i["code"] == "MATRIX_DEAD_PARAM_KEY"
                       for i in bad["issues"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMissingParamSource:
    """matrix 与默认标量都被删掉时，诊断必须报出来（不能假装算过）。"""

    def test_validate_pricing_rule_reports_unresolvable(self, system):
        # 直接把 waiting 的 matrix 清空，模拟「参数源被删」
        saved = system.sem.pricing["waiting"].pop("matrix")
        try:
            fee = {"fee_id": "F003", "fee_type": "waiting",
                   "occurred_at": "2026-11-05T10:00:00+08:00"}
            rule = {"props": {"rule_id": "PR-WAIT-01", "fee_type": "waiting",
                              "effective_from": "2026-01-01T00:00:00+08:00",
                              "effective_to": None}}
            findings = system._validate_pricing_rule(fee, rule)
            assert any(f["code"] == "PRICING_RULE_PARAMS_MISSING"
                       for f in findings)
        finally:
            system.sem.pricing["waiting"]["matrix"] = saved


class TestStaleInstanceParamsCopy:
    """存量库的规则实例参数副本不得再影响计算——矩阵是唯一源。

    事故：11-21 建的演示库里 PR-WAIT-01 实例还带着 by_vehicle_type +
    30/5/2.4 副本，当时 fee_analysis 把实例 params 喂进计算，于是
    大货车+雨天样本被盖回 12.00（4.2米厢货行），天气维度在诊断里看不见。
    现在计算不供实例参数，副本是**死数据**；ensure 仍会清掉它（数据卫生）。
    """

    @pytest.fixture()
    def stale(self):
        from freight.fact_store import FactStore as Store
        db = str(Path(tempfile.mkdtemp()) / "f.db")
        s = FreightSystem(db, fresh=True, seed=True, with_scenario=True)
        row = s.facts.get_row("pricing_rule:PR-WAIT-01")
        props = dict(Store.decode(row)["props"])
        props["params"] = {  # 旧库残留副本
            "free_minutes": 30, "block_minutes": 5, "per_block": 2.4,
            "by_vehicle_type": {"大货车": {"free_minutes": 30,
                                          "block_minutes": 5,
                                          "per_block": 2.4}},
        }
        s.facts.update_object("pricing_rule:PR-WAIT-01", props, None,
                              props.get("status"),
                              "2026-11-23T00:00:00+08:00")
        s.conn.commit()
        return s

    def _waiting_expected(self, s, waybill_id: str) -> float | None:
        r = s.fee_analysis(waybill_id)
        found = [f for f in r["findings"]
                 if f["code"] == "WAITING_FEE_MISMATCH"]
        return found[0]["expected"] if found else None

    def test_stale_copy_is_inert(self, stale):
        # 副本在也不再覆盖 matrix：大货车+雨天照样走 truck-rain 行 9.60
        assert self._waiting_expected(stale, "waybill:WB20261106002") == 9.6
        # 主运单零漂移
        assert self._waiting_expected(stale, "waybill:WB20261105001") == 12.0

    def test_ensure_clears_copy(self, stale):
        from freight import scenarios
        result = scenarios.ensure_pricing_samples(stale)
        assert any("PR-WAIT-01.params cleared" in d for d in result["done"])
        assert self._waiting_expected(stale, "waybill:WB20261106002") == 9.6
        assert self._waiting_expected(stale, "waybill:WB20261105001") == 12.0

    def test_ensure_is_idempotent(self, stale):
        from freight import scenarios
        scenarios.ensure_pricing_samples(stale)
        again = scenarios.ensure_pricing_samples(stale)
        assert again["skipped"] is True and again["done"] == []
