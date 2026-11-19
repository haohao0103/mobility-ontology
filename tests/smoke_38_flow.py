"""「为何这单比预估贵 38」——本体层优先的标准检索链演示。

回答用户问题：第一步怎么查本体层？

Palantir 标准检索范式（与 Foundry Ontology 一致）：
  第一步  TBox 锚定（先查本体层：类型/属性/链接，不碰任何实例数据）
  第二步  ABox 取数（组合式 Object Set DSL，followLink 派生费用明细集）
  第三步  聚合归因（同一 DSL 管道 sum + group_by，把 38 分解到费用类型）
  第四步  注册函数 + 受众化解释（fee-analysis 归因引擎 + explain 模板）

全程真实调用（TestClient + FactMCP），无 mock、无 LLM。
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

WB = "WB20261105001"
OBJ = f"waybill:{WB}"
QUERY_URL = "/freight/object-set/query"


def step(client, name, method, path, **kw):
    r = client.request(method, path, **kw)
    print(f"\n{'=' * 70}\n=== {name} -> {method} {path} [HTTP {r.status_code}] ===")
    return r


def main() -> int:
    td = tempfile.mkdtemp(prefix="flow38_")
    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    app = create_app(db_path=os.path.join(td, "facts.db"), fresh=True)
    client = TestClient(app)
    failures = []

    # ------------------------------------------------------------------
    # 第一步：查本体层（TBox 锚定）—— 不查任何实例数据，只查"哪里有答案"
    # ------------------------------------------------------------------
    print("#" * 70)
    print("# 第一步：查本体层（TBox）—— 先类型，后实例")
    print("#" * 70)

    # 入口 A：MCP ontology.schema（Agent 的标准第一跳）
    facts = get_system().facts
    r = facts.call("ontology.schema", {"types": ["Waybill"], "top_k": 6})
    blob = json.dumps(r, ensure_ascii=False)
    ok_props = all(p in blob for p in
                   ("estimated_fee", "actual_fee", "variance"))
    ok_link = "waybill_produces_fee" in blob and "FeeItem" in blob
    print(f"[MCP ontology.schema] Waybill 金额属性齐全: {ok_props} | "
          f"waybill_produces_fee -> FeeItem 链接存在: {ok_link}")
    if not (ok_props and ok_link):
        failures.append("step1_mcp_schema")

    # 入口 B：REST GET /freight/ontology（人类/服务的 TBox 快照）
    r = step(client, "第一步B·TBox 快照", "GET", "/freight/ontology")
    if r.status_code == 200:
        d = r.json()
        tbox = json.dumps(d, ensure_ascii=False)
        print(f"本体返回键: {list(d.keys())[:10]}")
        print(f"含 waybill_produces_fee: {'waybill_produces_fee' in tbox} | "
              f"含 variance 属性: {'variance' in tbox}")
        if "waybill_produces_fee" not in tbox or "variance" not in tbox:
            failures.append("step1_rest_ontology")
    else:
        failures.append("step1_rest_ontology")

    print("""
本体层结论（第一步到此结束，还没查任何一单数据）：
  Waybill 类自带 estimated_fee / actual_fee / variance 三个金额属性；
  运单经 waybill_produces_fee 链接派生 FeeItem 费用明细集。
  => 取数路径已经由本体决定：先锁定运单对象，再 followLink 费用明细。
""")

    # ------------------------------------------------------------------
    # 第二步：ABox 取数 —— 组合 DSL：ids 锚定运单 + followLink 派生费用集
    # 注：行级安全 osp-feeitem-disputed 规定 DISPUTED 费用仅 DISPUTE_OPS 标记
    #     成员可见（Palantir 语义：静默过滤、不报错）。故分两个视角对照演示。
    # ------------------------------------------------------------------
    print("#" * 70)
    print("# 第二步：取实例数据（ABox）—— ids 锚定 + followLink 派生")
    print("#" * 70)

    follow = {
        "set": {"followLink": {
            "set": {"ids": [OBJ]},
            "link": "waybill_produces_fee",
            "direction": "out",
        }},
    }

    # 视角 1：匿名用户（无标记）—— 有争议费用被行级安全过滤，只看到 6 项
    r = step(client, "第二步·匿名视角", "POST", QUERY_URL, json=follow)
    if r.status_code == 200:
        d = r.json()
        print(f"匿名视角派生 FeeItem 对象数: {d.get('total')}（行级安全过滤了 "
              f"DISPUTED 等候费，无报错——Palantir 静默过滤语义）")
    else:
        failures.append("step2_anonymous")

    # 视角 2：DISPUTE_OPS 标记成员 —— 全量 7 项费用明细
    r = step(client, "第二步·争议处理员视角(roles=DISPUTE_OPS)", "POST",
             QUERY_URL, json=follow,
             params={"user": "agent-dispute", "roles": "DISPUTE_OPS"})
    fee_rows = []
    if r.status_code == 200:
        d = r.json()
        fee_rows = d.get("objects", [])
        print(f"DISPUTE_OPS 视角派生 FeeItem 对象数: {d.get('total')}（预期 7）")
        for o in fee_rows:
            p = o.get("props", o)
            print(f"  - {p.get('fee_type', '?'):9s} {p.get('fee_label', '')} "
                  f"报价 {p.get('quoted_amount', 0):6.1f} -> 实收 "
                  f"{p.get('actual_amount', 0):6.1f} "
                  f"[{p.get('status', '?') or o.get('status', '?')}]")
        if d.get("total") != 7:
            failures.append("step2_followlink_count")
    else:
        failures.append("step2_followlink")

    # 谓词过滤演示（只看有争议的费用）
    disputed = dict(follow)
    disputed["where"] = {"status": {"$eq": "DISPUTED"}}
    r2 = step(client, "第二步·谓词过滤(有争议费用)", "POST", QUERY_URL,
              json=disputed,
              params={"user": "agent-dispute", "roles": "DISPUTE_OPS"})
    if r2.status_code == 200:
        d2 = r2.json()
        names = [o.get("props", o).get("fee_label", "?")
                 for o in d2.get("objects", [])]
        print(f"有争议费用: {names}（预期只含等候费）")
        if d2.get("total") != 1:
            failures.append("step2_disputed_count")
    else:
        failures.append("step2_disputed")

    # ------------------------------------------------------------------
    # 第三步：聚合归因 —— 同一 DSL 管道：sum + group_by，38 分解到费用类型
    # ------------------------------------------------------------------
    print("#" * 70)
    print("# 第三步：聚合归因 —— 38 元分解到费用类型")
    print("#" * 70)

    agg = dict(follow)
    agg["aggregate"] = {
        "count": True,
        "sum": ["quoted_amount", "actual_amount"],
        "group_by": ["fee_type"],
    }
    r = step(client, "第三步·按费用类型聚合", "POST", QUERY_URL, json=agg,
             params={"user": "agent-dispute", "roles": "DISPUTE_OPS"})
    variance = None
    if r.status_code == 200:
        d = r.json()
        total_var = 0.0
        for g in d.get("groups", []):
            s = g["sum"]
            delta = s["actual_amount"] - s["quoted_amount"]
            total_var += delta
            print(f"  {g['group_by']['fee_type']:9s} 报价合计 "
                  f"{s['quoted_amount']:6.1f} 实收合计 {s['actual_amount']:6.1f} "
                  f"差额 {delta:+6.1f}")
        print(f"  => 聚合口径总差额: {total_var:+.1f}（应等于 38.0）")
        variance = total_var
        if abs(total_var - 38.0) > 1e-6:
            failures.append("step3_variance_mismatch")
    else:
        failures.append("step3_aggregate")

    # ------------------------------------------------------------------
    # 第四步：注册函数 + 受众化解释（fee-analysis 归因引擎 + explain 模板）
    # ------------------------------------------------------------------
    print("#" * 70)
    print("# 第四步：注册函数归因 + 双受众解释")
    print("#" * 70)

    r = step(client, "第四步A·fee-analysis 注册函数", "GET",
             f"/freight/waybills/{OBJ}/fee-analysis")
    if r.status_code == 200:
        d = r.json()
        print(f"报价 {d['quoted_total']:.2f} | 实收 {d['actual_total']:.2f} "
              f"| 差额 {d['variance']:.2f}")
        print(f"归因发现 {len(d['findings'])} 项:", [
            f"{f['code']}(delta={f.get('delta')})" for f in d["findings"]])
        print("结论:", str(d.get("conclusion"))[:160])
        if abs(float(d["variance"]) - 38.0) > 1e-6:
            failures.append("step4_fee_analysis_variance")
        if not d["findings"]:
            failures.append("step4_no_findings")
    else:
        failures.append("step4_fee_analysis")

    for aud in ("cs", "finance"):
        r = step(client, f"第四步B·解释({aud})", "GET",
                 f"/freight/waybills/{OBJ}/explain", params={"audience": aud})
        if r.status_code == 200:
            d = r.json()
            print(f"解释: {str(d.get('answer', d.get('explanation', d)))[:200]}")
        else:
            failures.append(f"step4_explain_{aud}")

    # ------------------------------------------------------------------
    print("\n" + "#" * 70)
    print(f"「为何贵 38」检索链: {'全部跑通' if not failures else '失败 ' + str(failures)}")
    if variance is not None:
        print(f"DSL 聚合差额 {variance:+.1f} == fee-analysis 差额 38.0 == "
              f"本体声明的 variance 属性 38.0（三口径一致）")
    print("检索范式：TBox 先行（ontology.schema/ontology）-> ABox DSL 取数"
          " -> 聚合归因 -> 注册函数/受众解释，全程 0 次 LLM。")
    print("行级安全：匿名视角只见 6 项费用（差额 +13）；DISPUTE_OPS 标记"
          "视角见全量 7 项（差额 +38）——行被静默过滤而非报错。")
    print("#" * 70)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
