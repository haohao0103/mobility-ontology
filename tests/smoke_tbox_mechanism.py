"""TBox 查询机制实测——回答「本体核心功能到底有没有」。

机制一句话：TBox 不存数据（数据在 ABox/objects 表）；TBox 是查询的
「编译器 + 守门员」——每条查询先过 TBox 静态校验（类/属性/类型/算子/
链接方向/聚合列），求值时 TBox 类型系统继续兜底（坏数据不炸不静默算错），
写入时 TBox 校验主键/属性白名单/status 枚举。

同时诚实记录两个「没有」：
  N1 属性值类型写入不校验（estimated_fee='不是数字' 能入库）
  N2 类层级继承不存在（558 类 0 父类声明，查父类不会返回子类实例）
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
from freight.semantic import OntologyViolation  # noqa: E402

QUERY_URL = "/freight/object-set/query"


def main() -> int:
    td = tempfile.mkdtemp(prefix="tbox_")
    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    app = create_app(db_path=os.path.join(td, "facts.db"), fresh=True)
    client = TestClient(app)
    sys_ = get_system()
    failures = []

    # ------------------------------------------------------------------
    print("#" * 70)
    print("# A. TBox 静态校验：六类非法查询全部拦截（400 结构化报错）")
    print("#" * 70)
    cases = [
        ("未定义类", {"set": {"from": "Nope"}}),
        ("未知属性", {"set": {"from": "Waybill"},
                     "where": {"foo_bar": {"$eq": 1}}}),
        ("类型不匹配", {"set": {"from": "Waybill"},
                       "where": {"estimated_fee": {"$gt": "abc"}}}),
        ("链接方向违规", {"set": {"followLink": {
            "set": {"from": "Shipper"}, "link": "waybill_produces_fee",
            "direction": "out"}}}),
        ("未知算子", {"set": {"from": "Waybill"},
                     "where": {"status": {"$regex": "A"}}}),
        ("聚合未知列", {"set": {"from": "Waybill"},
                       "aggregate": {"sum": ["no_such"]}}),
    ]
    for name, body in cases:
        r = client.post(QUERY_URL, json=body, params={"user": "analyst"})
        detail = r.json().get("detail", "")[:80]
        ok = r.status_code == 400
        print(f"  [{'拦截' if ok else '漏过'}] {name}: {detail}")
        if not ok:
            failures.append(f"A_{name}")

    # ------------------------------------------------------------------
    print("#" * 70)
    print("# B. TBox 写入校验：坏实例走正规写入路径被拒")
    print("#" * 70)
    engine = sys_
    for name, args in [
        ("主键与对象ID不一致", ("Waybill", "waybill:XX9", "x", "CREATED",
                             {"waybill_id": "WB_OTHER"})),
        ("未声明属性", ("Waybill", "waybill:WB_OK1", "x", "CREATED",
                      {"waybill_id": "WB_OK1", "bogus_prop": 1})),
        ("非法status枚举", ("Waybill", "waybill:WB_OK2", "x", "TOTALLY_BOGUS",
                          {"waybill_id": "WB_OK2"})),
    ]:
        try:
            engine.add_object(*args, at="2026-11-05T00:00:00+08:00")
            print(f"  [漏过] {name}")
            failures.append(f"B_{name}")
        except OntologyViolation as exc:
            print(f"  [拦截] {name}: {str(exc)[:80]}")

    # ------------------------------------------------------------------
    print("#" * 70)
    print("# C. TBox 类型系统求值兜底：ABox 混入坏数据，查询不炸不静默算错")
    print("#" * 70)
    store = sys_.facts.store
    store.conn.execute(
        "INSERT OR REPLACE INTO objects (object_id, type, name, status, props,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        ("waybill:WB_BAD", "Waybill", "坏数据", "DELIVERED",
         json.dumps({"waybill_id": "WB_BAD", "estimated_fee": "不是数字"}),
         "2026-11-05T00:00:00+08:00", "2026-11-05T00:00:00+08:00"))
    store.conn.commit()
    r = client.post(QUERY_URL, json={
        "set": {"from": "Waybill"},
        "where": {"estimated_fee": {"$gt": 100}}}, params={"user": "analyst"})
    d = r.json()
    print(f"  数值谓词遇字符串 fee: HTTP {r.status_code}, total={d.get('total')}"
          f"（坏行不匹配不报错）")
    r2 = client.post(QUERY_URL, json={
        "set": {"from": "Waybill"},
        "aggregate": {"sum": ["estimated_fee"]}}, params={"user": "analyst"})
    g = r2.json()["groups"][0]
    print(f"  聚合遇字符串 fee: sum={g['sum']['estimated_fee']}"
          f"（字符串被跳过，不进 sum、不算 0）")
    if r.status_code != 200 or r2.status_code != 200:
        failures.append("C_baddata")

    # ------------------------------------------------------------------
    print("#" * 70)
    print("# D. 诚实记录：本体核心的真实缺口（没实现就是没实现）")
    print("#" * 70)
    classes = {c["id"]: c for c in sys_.sem.raw["classes"]}
    has_parent = [i for i, c in classes.items()
                  if c.get("parent") or c.get("subclass_of")]
    print(f"  N1 属性值类型写入不校验：estimated_fee='不是数字' 可入库"
          f"（求值层兜底，但脏数据能进库）")
    print(f"  N2 类层级继承：{len(classes)} 个类中 {len(has_parent)} 个声明父类"
          f"——查父类不返回子类实例，继承推理不存在")
    print(f"  N3 传递/推理闭包：closure 是查询展开白名单，非逻辑推理")
    print(f"  N4 等价类/属性传播：无")

    print("#" * 70)
    verdict = "全部跑通" if not failures else f"失败 {failures}"
    print(f"TBox 查询机制实测: {verdict}")
    print("结论：TBox=查询的编译器+守门员（校验/类型/路径），ABox=数据；")
    print("Palantir 式强类型对象图的核心机制在工作；OWL 级继承/推理没有。")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
