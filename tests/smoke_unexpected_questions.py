"""承重测试：本体能否回答「从未被预设过」的问题。

背景：用户质疑——如果 Waybill 的金额属性、waybill_produces_fee 链接都是
为了回答「为何贵 38」预设的，那本体就没用。本脚本证伪该假设：
取 4 个「贵38演示链路从未出现过」的全新问题，全部用同一套标准检索范式
（第一步 TBox 锚定 -> 第二步 followLink DSL 取数）回答。

  Q0  用户怎么知道查 Waybill？ -> 类型发现（object_type_search 语义搜索）
  Q1  这单货多重、多少托？      -> order_generates_waybill(入边) + order_has_cargo
  Q2  司机哪个车队、资质何时到期 -> waybill_assigned_to_driver
  Q3  车辆载重够不够拉这货？    -> waybill_uses_vehicle
  Q4  装货卸货各花了多久？      -> Waybill 自身属性（不碰费用路径）

若本体是「为贵38定制」，这些路径不应存在或不应可查。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api import create_app  # noqa: E402
from freight.api import get_system  # noqa: E402

OBJ = "waybill:WB20261105001"
QUERY_URL = "/freight/object-set/query"


def main() -> int:
    td = tempfile.mkdtemp(prefix="unexp_")
    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    app = create_app(db_path=os.path.join(td, "facts.db"), fresh=True)
    client = TestClient(app)
    facts = get_system().facts
    failures = []

    # ------------------------------------------------------------------
    # Q0 用户怎么知道查 Waybill？——第一步本来就是语义类型发现，不是预设
    # ------------------------------------------------------------------
    print("=" * 70)
    print("Q0 用户说「运单」「费用」-> 第一步用 object_type_search 发现类型")
    r = facts.call("ontology.object_type_search", {"question": "运单", "top_k": 4})
    blob = json.dumps(r, ensure_ascii=False)
    found_wb = "Waybill" in blob
    print(f"用自然语言搜类型 -> 命中 Waybill: {found_wb}")
    if not found_wb:
        failures.append("q0_type_search")
    # 反向验证：搜一个跟运单无关的词，不应该把 Waybill 排在前面（非硬编码）
    r_neg = facts.call("ontology.object_type_search",
                       {"question": "司机 车队 资质", "top_k": 5})
    blob_neg = json.dumps(r_neg, ensure_ascii=False)
    print(f"换问司机/车队 -> 命中 Driver: {'Driver' in blob_neg}（类型发现随问题变，"
          f"不是固定返回 Waybill）")
    if "Driver" not in blob_neg:
        failures.append("q0_type_search_negative")

    def follow_out(link, obj_id=OBJ):
        body = {"set": {"followLink": {"set": {"ids": [obj_id]},
                                       "link": link, "direction": "out"}}}
        resp = client.post(QUERY_URL, json=body,
                           params={"user": "analyst"})
        assert resp.status_code == 200, f"{link}: {resp.status_code} {resp.text[:200]}"
        return [o["props"] for o in resp.json()["objects"]]

    def follow_in(link):
        body = {"set": {"followLink": {"set": {"ids": [OBJ]},
                                       "link": link, "direction": "in"}}}
        resp = client.post(QUERY_URL, json=body,
                           params={"user": "analyst"})
        assert resp.status_code == 200, f"{link}: {resp.status_code} {resp.text[:200]}"
        return [o["props"] for o in resp.json()["objects"]]

    # ------------------------------------------------------------------
    # Q1 这单货多重、多少托？（贵38链路从未经过 order->cargo 路径）
    # ------------------------------------------------------------------
    cargos = []
    print("=" * 70)
    print("Q1 全新问题：这单货多重、多少托？")
    try:
        orders = follow_in("order_generates_waybill")
        order_ids = [o.get("order_id") for o in orders]
        print(f"  入边 order_generates_waybill -> 订单: {order_ids}")
        body = {"set": {"followLink": {
            "set": {"followLink": {"set": {"ids": [OBJ]},
                                   "link": "order_generates_waybill",
                                   "direction": "in"}},
            "link": "order_has_cargo", "direction": "out"}}}
        resp = client.post(QUERY_URL, json=body,
                           params={"user": "analyst"})
        assert resp.status_code == 200, resp.text[:200]
        cargos = [o["props"] for o in resp.json()["objects"]]
        for c in cargos:
            print(f"  货物 {c.get('name', '?')}: {c.get('weight_kg')} kg, "
                  f"{c.get('package_count')} {c.get('package_type', '件')}")
        if not cargos or cargos[0].get("weight_kg") != 3200:
            failures.append("q1_cargo")
    except Exception as exc:  # noqa: BLE001
        print(f"  Q1 失败: {exc}")
        failures.append("q1_cargo")

    # ------------------------------------------------------------------
    # Q2 司机哪个车队、资质何时到期？（waybill_assigned_to_driver 路径）
    # ------------------------------------------------------------------
    print("=" * 70)
    print("Q2 全新问题：司机哪个车队、资质何时到期？")
    try:
        drivers = follow_out("waybill_assigned_to_driver")
        for d in drivers:
            print(f"  普通分析师视角 -> 车队={d.get('fleet')}, "
                  f"资质={d.get('qualification')}, "
                  f"姓名={d.get('driver_name')}（PII 列级脱敏为 null）")
        # PII 标记成员可看到司机姓名
        body = {"set": {"followLink": {"set": {"ids": [OBJ]},
                                       "link": "waybill_assigned_to_driver",
                                       "direction": "out"}}}
        resp = client.post(QUERY_URL, json=body,
                           params={"user": "hr", "roles": "PII"})
        d2 = resp.json()["objects"][0]["props"]
        print(f"  PII 标记视角 -> 姓名={d2.get('driver_name')}")
        if not drivers or drivers[0].get("fleet") != "华东车队":
            failures.append("q2_driver")
        if drivers and drivers[0].get("driver_name") is not None:
            failures.append("q2_pii_not_masked")
        if d2.get("driver_name") != "张师傅":
            failures.append("q2_pii_not_visible")
    except Exception as exc:  # noqa: BLE001
        print(f"  Q2 失败: {exc}")
        failures.append("q2_driver")

    # ------------------------------------------------------------------
    # Q3 车辆载重够不够？（waybill_uses_vehicle + 与 Q1 货重交叉判断）
    # ------------------------------------------------------------------
    print("=" * 70)
    print("Q3 全新问题：车辆载重够不够拉这货？")
    try:
        vehicles = follow_out("waybill_uses_vehicle")
        for v in vehicles:
            print(f"  车辆 {v.get('plate', '?')} {v.get('vehicle_type', '?')}: "
                  f"核定载重 {v.get('capacity_t')} t")
        if vehicles and cargos:
            cap = vehicles[0].get("capacity_t", 0)
            wt = cargos[0].get("weight_kg", 0) / 1000
            verdict = "够" if cap >= wt else "不够"
            print(f"  => 载重 {cap}t vs 货重 {wt:.1f}t: {verdict}")
            if cap < wt:
                failures.append("q3_capacity")
    except Exception as exc:  # noqa: BLE001
        print(f"  Q3 失败: {exc}")
        failures.append("q3_vehicle")

    # ------------------------------------------------------------------
    # Q4 装货卸货各花多久？（纯 Waybill 自身属性，不碰任何费用路径）
    # ------------------------------------------------------------------
    print("=" * 70)
    print("Q4 全新问题：装货卸货各花多久？")
    try:
        from datetime import datetime

        def mins(a, b):
            ta = datetime.fromisoformat(a)
            tb = datetime.fromisoformat(b)
            return (tb - ta).total_seconds() / 60

        r = client.post(QUERY_URL, json={"set": {"ids": [OBJ]}},
                        params={"user": "analyst"})
        wb = r.json()["objects"][0]["props"]
        load_m = mins(wb["loading_started_at"], wb["loading_ended_at"])
        unload_m = mins(wb["unloading_started_at"], wb["unloading_ended_at"])
        print(f"  装货 {load_m:.0f} 分钟, 卸货 {unload_m:.0f} 分钟"
              f"（数据来自运单生命周期属性，非费用路径）")
        if load_m <= 0 or unload_m <= 0:
            failures.append("q4_duration")
    except Exception as exc:  # noqa: BLE001
        print(f"  Q4 失败: {exc}")
        failures.append("q4_duration")

    print("=" * 70)
    print(f"未预设问题承重测试: {'4/4 + 类型发现 2/2 全部跑通' if not failures else '失败 ' + str(failures)}")
    print("结论：同一套 TBox锚定->followLink 范式回答了贵38链路从未出现过的"
          "货重/车队/载重/装卸时长问题——本体不是为单一问题预设的。")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
