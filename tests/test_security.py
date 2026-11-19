"""P3 安全层承重测试：markings 强制控制 + 行级/列级 + scope token + 策略模拟。"""
from __future__ import annotations

import pytest

from enhancements.security import (
    SecurityContext, SecurityEngine, AccessDenied,
)


def _rows_of(enhanced, class_id: str) -> list[dict]:
    import json as _json
    rows = enhanced.store.all("SELECT * FROM objects WHERE type=?", (class_id,))
    return [
        {"object_id": r["object_id"], "status": r["status"],
         "props": _json.loads(r["props"])}
        for r in rows
    ]


class TestColumnLevel:
    def test_pii_masked_for_plain_user(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "Shipper")
        assert rows, "Shipper 种子数据缺失"
        ctx = SecurityContext(user="alice")  # 无任何标记
        masked = eng.mask_properties("Shipper", rows[0]["props"], ctx)
        assert masked["shipper_name"] is None
        assert masked["contact"] is None

    def test_pii_visible_for_marked_user(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "Shipper")
        ctx = SecurityContext.with_roles("hr", {"PII"})
        masked = eng.mask_properties("Shipper", rows[0]["props"], ctx)
        assert masked["shipper_name"] is not None

    def test_finance_masked(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "Waybill")
        ctx = SecurityContext.with_roles("ops", {"PII"})  # 有 PII 无 FINANCE
        masked = eng.mask_properties("Waybill", rows[0]["props"], ctx)
        assert masked["actual_fee"] is None
        assert masked["estimated_fee"] is None

    def test_mandatory_control_no_role_bypass(self, enhanced):
        """强制控制语义（Palantir）：角色再高，无标记也读不到。"""
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "Shipper")
        ctx = SecurityContext.with_roles("superadmin", {"superadmin", "root", "owner"})
        masked = eng.mask_properties("Shipper", rows[0]["props"], ctx)
        assert masked["shipper_name"] is None

    def test_load_bearing_delete_marking_restores_visibility(self, enhanced, make_ext):
        """承重：本体 JSON 删掉 PII 标记定义 -> 脱敏立即失效。"""

        def mutate(raw):
            raw["markings"] = [m for m in raw["markings"] if m["id"] != "PII"]
            for p in raw["property_types"]:
                if p.get("marking") == "PII":
                    p["marking"] = None
            raw["property_security_policies"] = [
                ps for ps in raw["property_security_policies"]
                if ps.get("required_marking") != "PII"
            ]
            return raw

        eng = SecurityEngine(make_ext(mutate))
        rows = _rows_of(enhanced, "Shipper")
        ctx = SecurityContext(user="alice")
        masked = eng.mask_properties("Shipper", rows[0]["props"], ctx)
        assert masked["shipper_name"] is not None


class TestRowLevel:
    def _disputed_fee(self, enhanced):
        rows = _rows_of(enhanced, "FeeItem")
        disputed = [r for r in rows if r["status"] == "DISPUTED"]
        return rows, disputed

    def test_disputed_row_hidden_without_marking(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows, disputed = self._disputed_fee(enhanced)
        assert disputed, "种子数据缺 DISPUTED FeeItem"
        ctx = SecurityContext(user="ops")
        visible = eng.filter_objects("FeeItem", rows, ctx)
        assert all(r["status"] != "DISPUTED" for r in visible)

    def test_disputed_row_visible_with_marking(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows, _ = self._disputed_fee(enhanced)
        ctx = SecurityContext.with_roles("dispute", {"DISPUTE_OPS"})
        visible = eng.filter_objects("FeeItem", rows, ctx)
        assert any(r["status"] == "DISPUTED" for r in visible)

    def test_load_bearing_delete_policy_restores_rows(self, enhanced, make_ext):
        """承重：删掉行级策略 -> DISPUTED 行对所有人可见。"""

        def mutate(raw):
            raw["object_security_policies"] = []
            return raw

        eng = SecurityEngine(make_ext(mutate))
        rows, _ = self._disputed_fee(enhanced)
        ctx = SecurityContext(user="ops")
        visible = eng.filter_objects("FeeItem", rows, ctx)
        assert any(r["status"] == "DISPUTED" for r in visible)


class TestScopeToken:
    def test_out_of_scope_function_blocked(self, enhanced):
        ctx = SecurityContext.with_roles("agent-readonly", {"freight-core"})
        ctx.scope_token = enhanced.ext.scope_tokens["default_token"]
        eng = SecurityEngine(enhanced.ext)
        with pytest.raises(AccessDenied, match="scope token"):
            eng.check_scope(ctx, "function", "evidence_chain")

    def test_in_scope_function_allowed(self, enhanced):
        ctx = SecurityContext.with_roles("agent-readonly", {"freight-core"})
        ctx.scope_token = enhanced.ext.scope_tokens["default_token"]
        eng = SecurityEngine(enhanced.ext)
        eng.check_scope(ctx, "function", "fee_breakdown")  # 不抛即通过

    def test_class_scope_blocked(self, enhanced):
        ctx = SecurityContext.with_roles("agent-readonly", {"freight-core"})
        token = dict(enhanced.ext.scope_tokens["default_token"])
        token["classes"] = ["Waybill"]
        ctx.scope_token = token
        eng = SecurityEngine(enhanced.ext)
        with pytest.raises(AccessDenied):
            eng.check_scope(ctx, "class", "Shipper")

    def test_load_bearing_shrink_token_shrinks_tools(self, enhanced, make_ext):
        """承重：令牌 scope 收窄（只改 JSON）-> 工具面跟着收窄。"""

        def mutate(raw):
            raw["scope_tokens"]["default_token"]["functions"] = ["fee_breakdown"]
            return raw

        ext = make_ext(mutate)
        eng = SecurityEngine(ext)
        ctx = SecurityContext.with_roles("agent", {"freight-core"})
        ctx.scope_token = ext.scope_tokens["default_token"]
        with pytest.raises(AccessDenied):
            eng.check_scope(ctx, "function", "quote_reconcile")

    def test_allowed_tools_filter(self, enhanced):
        ctx = SecurityContext.with_roles("agent", {"freight-core"})
        ctx.scope_token = enhanced.ext.scope_tokens["default_token"]
        eng = SecurityEngine(enhanced.ext)
        allowed = eng.allowed_tools(ctx, ["ontology.catalog", "facts.query", "evil.tool"])
        assert "evil.tool" not in allowed
        assert "ontology.catalog" in allowed


class TestSecureReadPath:
    def test_secure_get_object_masks(self, enhanced):
        ctx = SecurityContext(user="alice")
        shipper_id = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='Shipper' LIMIT 1"
        )["object_id"]
        out = enhanced.secure_get_object(shipper_id, ctx)
        assert out["props"]["shipper_name"] is None

    def test_secure_get_object_visible_with_marking(self, enhanced):
        ctx = SecurityContext.with_roles("hr", {"PII"})
        shipper_id = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='Shipper' LIMIT 1"
        )["object_id"]
        out = enhanced.secure_get_object(shipper_id, ctx)
        assert out["props"]["shipper_name"] is not None

    def test_row_level_denied_raises(self, enhanced):
        ctx = SecurityContext(user="ops")
        disputed = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='FeeItem' AND status='DISPUTED' LIMIT 1"
        )
        if disputed is None:
            pytest.skip("无 DISPUTED 种子数据")
        with pytest.raises(AccessDenied, match="行级安全策略"):
            enhanced.secure_get_object(disputed["object_id"], ctx)


class TestPolicySimulator:
    def test_simulate_matrix(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "FeeItem")
        ctx = SecurityContext.with_roles("auditor", {"PII", "FINANCE"})
        out = eng.simulate("FeeItem", rows, ctx)
        assert out["total_rows"] == len(rows)
        assert out["visible_rows"] <= out["total_rows"]
        assert isinstance(out["property_visibility"], dict)

    def test_simulate_plain_user_sees_fewer(self, enhanced):
        eng = SecurityEngine(enhanced.ext)
        rows = _rows_of(enhanced, "FeeItem")
        plain = eng.simulate("FeeItem", rows, SecurityContext(user="nobody"))
        rich = eng.simulate(
            "FeeItem", rows,
            SecurityContext.with_roles("ops", {"PII", "FINANCE", "DISPUTE_OPS"}),
        )
        assert plain["visible_rows"] < rich["visible_rows"]
