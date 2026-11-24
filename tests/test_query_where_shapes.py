"""ontology.query 的 where 形状归一（HTTP 链路实测事故回归）。

事故原样（vc_step_10）：模型把单个过滤条件写成
  {"type": "FeeVariance",
   "where": {"property": "waybill_id", "operator": "eq",
             "value": "waybill:WB20261105001"}}
旧代码把 where 当「扁平属性表」，键名 "property" 被当属性名校验 →
SCHEMA_ERROR「FeeVariance 不允许的属性: property」，错误信息完全指错
方向，模型无从自纠（它写的 property/operator/value 其实是合法的过滤
结构字段，只是外层少包了一层数组）。
"""
import json

import pytest

from freight.engine import FreightSystem


@pytest.fixture()
def system(tmp_path):
    sys_ = FreightSystem(str(tmp_path / "facts.db"), fresh=True, seed=True)
    yield sys_
    sys_.conn.close()


def _waiting_fee_items(system):
    res = system.facts.query_objects("FeeItem", where={"fee_type": "waiting"})
    assert "error" not in res
    assert res["count"] >= 1, "seed 库里应有 waiting 费用项"
    return res


def test_single_filter_object_is_accepted(system):
    """事故形状：单个 {property, operator, value} dict 必须被归一接受。"""
    _waiting_fee_items(system)
    res = system.facts.query_objects(
        "FeeItem",
        where={"property": "fee_type", "operator": "eq", "value": "waiting"},
    )
    assert "error" not in res
    assert res["count"] >= 1
    for obj in res["objects"]:
        assert obj["properties"]["fee_type"] == "waiting"


def test_single_filter_object_with_op_key(system):
    """op 键（而非 operator）同样支持。"""
    res = system.facts.query_objects(
        "FeeItem",
        where={"property": "fee_type", "op": "eq", "value": "waiting"},
    )
    assert "error" not in res and res["count"] >= 1


def test_flat_property_dict_still_works(system):
    """扁平属性表（既有契约）不受影响。"""
    res = system.facts.query_objects("FeeItem", where={"fee_type": "waiting"})
    assert "error" not in res and res["count"] >= 1


def test_filter_list_still_works(system):
    """过滤数组（既有契约）不受影响。"""
    res = system.facts.query_objects(
        "FeeItem",
        where=[{"property": "fee_type", "op": "eq", "value": "waiting"}],
    )
    assert "error" not in res and res["count"] >= 1


def test_filter_list_multi_condition(system):
    """多条件数组 + 合法操作符仍照常编译。"""
    res = system.facts.query_objects(
        "FeeItem",
        where=[{"property": "fee_type", "op": "eq", "value": "waiting"},
               {"property": "status", "op": "in", "value": ["DISPUTED", "NORMAL"]}],
    )
    assert "error" not in res
    assert res["count"] >= 1


def test_invalid_property_error_carries_where_examples(system):
    """真属性名非法时，错误必须带 where 正确示例与提示，模型才能自纠。"""
    from freight.semantic import BusinessReject
    with pytest.raises(BusinessReject) as exc:
        system.facts.query_objects(
            "FeeItem",
            where={"property": "no_such_prop", "operator": "eq", "value": "x"},
        )
    details = getattr(exc.value, "details", None) or {}
    blob = json.dumps(details, ensure_ascii=False, default=str)
    assert "where_examples" in blob
    assert "过滤结构字段" in blob
    assert "allowed_properties" in blob


def test_flat_dict_unknown_key_still_rejected(system):
    """扁平表里放不存在的属性名仍必须拒绝（契约不放水）。"""
    from freight.semantic import BusinessReject
    with pytest.raises(BusinessReject):
        system.facts.query_objects("FeeItem", where={"no_such_prop": "x"})


def test_none_and_empty_where(system):
    """空 where 仍返回全量（既有行为）。"""
    res = system.facts.query_objects("FeeItem")
    assert "error" not in res
    assert res["count"] >= 1
