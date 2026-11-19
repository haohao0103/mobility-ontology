"""一单货完整确定性诊断链（无 LLM）。

模拟 web/order-ontology.html 页面的真实调用序列 + 页面背后的完整证据链：
  事实总览 -> 单据事实 -> 差异归因 -> 双受众解释 -> 对象图谱 -> 血缘溯源
每一步都是真实 HTTP 调用（TestClient），无 mock、无 LLM。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api import create_app  # noqa: E402

WB = "WB20261105001"
OBJ = f"waybill:{WB}"


def step(client, name, method, path, **kw):
    r = client.request(method, path, **kw)
    print(f"\n=== {name} -> {method} {path} [HTTP {r.status_code}] ===")
    return r


def main() -> int:
    td = tempfile.mkdtemp(prefix="one_order_")
    os.environ.pop("OPENAI_CHAT_API_KEY", None)
    app = create_app(db_path=os.path.join(td, "facts.db"), fresh=True)
    client = TestClient(app)
    failures = []

    # 1. 事实总览（页面加载第一步）
    r = step(client, "事实总览", "GET", "/freight/facts/overview")
    if r.status_code == 200:
        d = r.json()
        print("对象数:", d.get("objects", d.get("stats", {}).get("objects", "?")),
              "| 顶层键:", list(d.keys())[:8])
    else:
        failures.append("overview")

    # 2. 单据事实（页面第二步）
    r = step(client, "单据事实", "GET", f"/freight/facts/query/{OBJ}")
    if r.status_code == 200:
        d = r.json()
        keys = list(d.keys())
        print("返回键:", keys[:10])
        blob = json.dumps(d, ensure_ascii=False)
        print("含运单号:", WB in blob, "| 含金额:", "fee" in blob.lower())
    else:
        failures.append("facts_query")

    # 3. 差异归因（核心：为什么贵 38）
    r = step(client, "差异归因", "GET", f"/freight/waybills/{OBJ}/fee-analysis")
    if r.status_code == 200:
        d = r.json()
        print(f"报价: {d['quoted_total']:.2f} | 实收: {d['actual_total']:.2f} "
              f"| 差额: {d['variance']:.2f}")
        print(f"明细 {len(d['breakdown'])} 项:", [
            f"{b.get('fee_type', b.get('fee_label', '?'))}"
            for b in d["breakdown"]])
        print(f"发现 {len(d['findings'])} 项:", [
            f"{f['code']}(delta={f.get('delta')})" for f in d["findings"]])
        print(f"证据 {len(d.get('evidence', []))} 条 | 结算单 {len(d.get('settlement_ids', []))} | "
              f"异常 {len(d.get('exception_ids', []))}")
        print("结论:", str(d.get("conclusion"))[:160])
    else:
        failures.append("fee_analysis")

    # 4. 双受众解释（模板化，无 LLM）
    for aud in ("cs", "finance"):
        r = step(client, f"解释({aud})", "GET",
                 f"/freight/waybills/{OBJ}/explain", params={"audience": aud})
        if r.status_code == 200:
            d = r.json()
            print("解释:", str(d.get("answer", d.get("explanation", d)))[:180])
        else:
            failures.append(f"explain_{aud}")

    # 5. 对象图谱（一单货牵出的关联网络）
    r = step(client, "对象图谱", "GET", f"/freight/objects/{OBJ}/graph")
    if r.status_code == 200:
        d = r.json()
        nodes = d.get("nodes", [])
        links = d.get("links", d.get("edges", []))
        print(f"节点 {len(nodes)} 个:", sorted({n.get('type', '?') for n in nodes}))
        print(f"边 {len(links)} 条")
    else:
        failures.append("graph")

    # 6. 血缘溯源（结论的证据链）
    r = step(client, "血缘溯源", "GET", f"/freight/objects/{OBJ}/trace")
    if r.status_code == 200:
        d = r.json()
        blob = json.dumps(d, ensure_ascii=False)
        print("返回键:", list(d.keys())[:10])
        print("含 ETC/证据引用:", "evidence" in blob.lower() or "EV" in blob)
    else:
        failures.append("trace")

    print("\n" + "=" * 70)
    print(f"一单货确定性诊断链: {'全部跑通' if not failures else '失败 ' + str(failures)}")
    print("全程 0 次 LLM 调用（无 OPENAI_CHAT_API_KEY 环境）。")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
