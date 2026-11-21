#!/usr/bin/env python3
"""演示自检：上台前 5 分钟跑一遍，确认所有演示路径可用。

演示当天最贵的不是"系统没做好"，而是"现场才发现某条路径挂了"。
本脚本按演示动线逐项检查，并给出**耗时基线**（知道该等多久，心里不慌）。

用法：
    python scripts/demo_gate.py            # 全量自检（含一次真实 LLM 调用）
    python scripts/demo_gate.py --fast     # 跳过 LLM 推理链，只查接口
    python scripts/demo_gate.py --json     # 机器可读输出
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8765")
WAYBILL = "waybill:WB20261105001"

# 演示动线上的每一步：名称 + 路径 + 期望耗时上限（秒）
CHECKS = [
    ("服务存活", "/freight/health", 5),
    ("本体规模", "/freight/ontology", 10),
    ("状态机视图", "/freight/ontology/state-machines", 10),
    ("对象详情", f"/freight/objects/{WAYBILL}", 10),
    ("对象状态轨迹", f"/freight/objects/waybill:SCN01/state-history", 10),
    ("费用差异诊断（三口径 + 来源覆盖）", f"/freight/waybills/{WAYBILL}/fee-analysis", 15),
    ("面向财务的解释", f"/freight/waybills/{WAYBILL}/explain?audience=finance", 15),
    ("对象图（两跳）", f"/freight/objects/{WAYBILL}/graph?depth=2", 10),
    ("证据链", "/freight/mcp/tools", 10),
    ("审计流水", "/freight/audit", 10),
]


def _get(path: str, timeout: float = 60.0):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)[:160]}


def _post(path: str, body: dict | None = None, timeout: float = 300.0):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body or {}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"error": str(e)[:160]}


def check_endpoints() -> list[dict]:
    rows = []
    for name, path, budget in CHECKS:
        t0 = time.time()
        status, payload = _get(path)
        elapsed = round(time.time() - t0, 2)
        ok = status == 200
        detail = ""
        if ok and isinstance(payload, dict):
            if "knowledge" in payload:
                k = payload["knowledge"]
                detail = f"{k.get('classes')} 类 / {k.get('relations')} 链接 / {k.get('rules')} 规则 / {k.get('actions')} 动作"
            elif "machines" in payload:
                detail = f"{len(payload['machines'])} 个状态机 / {sum(len(m['transitions']) for m in payload['machines'])} 条迁移边"
            elif "findings" in payload:
                sc = payload.get("source_coverage") or {}
                detail = (f"报价 {payload.get('quoted_total')} / 实收 {payload.get('actual_total')} / "
                          f"差异 {payload.get('variance')}；来源已解析 "
                          f"{sc.get('resolved')}/{sc.get('fee_count')}")
            elif "steps" in payload and "current_status" in payload:
                detail = f"当前 {payload.get('current_status')} / {payload.get('step_count')} 步轨迹"
            elif "tools" in payload:
                detail = f"{len(payload['tools'])} 个工具"
        elif not ok:
            detail = str(payload)[:120]
        rows.append({
            "name": name, "path": path, "status": status, "ok": ok,
            "elapsed_s": elapsed, "budget_s": budget,
            "slow": ok and elapsed > budget,
            "detail": detail,
        })
    return rows


def check_llm() -> dict:
    """LLM 连通性 + 一次真实调用的耗时基线。"""
    t0 = time.time()
    try:
        from freight.agent import LLMClient

        client = LLMClient()
        out = client.chat([{"role": "user", "content": "用一句话回答：本体是什么？"}])
        return {"ok": bool(out), "model": client.model, "endpoint": client.base,
                "elapsed_s": round(time.time() - t0, 2),
                "retry_attempts": client.retry_attempts,
                "sample": (out or "")[:60]}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "elapsed_s": round(time.time() - t0, 2),
                "error": f"{type(exc).__name__}: {exc}"[:200]}


def check_chain(full: bool = True) -> dict:
    """一键跑完推理链（演示的高潮环节）：给出真实耗时基线。"""
    question = urllib.parse.quote("这单为什么比预估多 38 元？")
    status, data = _post(
        f"/freight/waybills/{WAYBILL}/reasoning-chain/start?max_steps=4&question={question}")
    if status != 200 or not isinstance(data, dict):
        return {"ok": False, "status": status, "detail": str(data)[:160]}
    t0 = time.time()
    status, result = _post(f"/freight/reasoning-chain/{data.get('session_id')}/run")
    elapsed = round(time.time() - t0, 2)
    answer = ""
    if isinstance(result, dict):
        # 结论在 steps 的最后一步里（action.answer），顶层不直接给
        answer = str(result.get("final_answer") or result.get("answer") or "")
        if not answer:
            for step in reversed(result.get("steps") or []):
                act = step.get("action") or {}
                if isinstance(act, dict) and act.get("answer"):
                    answer = str(act["answer"])
                    break
    return {
        "ok": status == 200 and bool(result.get("finished") if isinstance(result, dict) else False),
        "status": status,
        "elapsed_s": elapsed,
        "auto_steps": (result or {}).get("auto_steps"),
        "model": (result or {}).get("model"),
        "answer_chars": len(answer),
        "participants": ((result or {}).get("summary") or {}).get("participants"),
        "detail": (result or {}).get("agent", {}).get("error") or "",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过 LLM 推理链")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    started = time.time()
    endpoints = check_endpoints()
    llm = check_llm()
    chain = {"ok": True, "skipped": True} if args.fast else check_chain(True)

    rows = list(endpoints)
    ok = all(r["ok"] for r in rows) and llm.get("ok") and chain.get("ok")
    out = {
        "ok": ok,
        "base": BASE,
        "elapsed_s": round(time.time() - started, 2),
        "endpoints": rows,
        "llm": llm,
        "chain": chain,
        "advice": _advice(rows, llm, chain),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"演示自检 · {BASE}")
    print("=" * 78)
    for r in rows:
        mark = "OK " if r["ok"] else "FAIL"
        slow = " (慢)" if r["slow"] else ""
        print(f"  [{mark}] {r['name']:32s} {r['elapsed_s']:6.2f}s{slow}  {r['detail']}")
    print("-" * 78)
    mark = "OK " if llm.get("ok") else "FAIL"
    print(f"  [{mark}] LLM 连通性{'':20s} {llm.get('elapsed_s', 0):6.2f}s  "
          f"{llm.get('model')} {llm.get('error', '')}")
    if chain.get("skipped"):
        print("  [--] 推理链（一键跑完）            已跳过（--fast）")
    else:
        mark = "OK " if chain.get("ok") else "FAIL"
        print(f"  [{mark}] 推理链（一键跑完）{'':16s} {chain.get('elapsed_s', 0):6.2f}s  "
              f"步骤 {chain.get('auto_steps')} 参与方 {chain.get('participants')} "
              f"{chain.get('detail', '')}")
    print("=" * 78)
    for line in out["advice"]:
        print(f"  · {line}")
    print(f"\n结论：{'演示就绪' if ok else '存在阻塞项，先修再上台'}"
          f"（总耗时 {out['elapsed_s']}s）")
    return 0 if ok else 1


def _advice(rows, llm, chain) -> list[str]:
    advice = []
    slow = [r["name"] for r in rows if r.get("slow")]
    if slow:
        advice.append(f"这些接口比预期慢：{', '.join(slow)}——演示前预热一次")
    if not llm.get("ok"):
        advice.append("LLM 不可用：改走**确定性本体路径**演示（结论不依赖大模型，"
                      "这本身就是卖点：同一个问题，本体路径 0.1 秒出结论且可复现）")
    if chain.get("elapsed_s", 0) > 90:
        advice.append(f"推理链要 {chain['elapsed_s']}s：先讲完本体结构，再点『一键跑完』，"
                      f"期间用别的话术过渡，别让领导盯着空白等待")
    if not chain.get("ok") and not chain.get("skipped"):
        advice.append(f"推理链未跑完：{chain.get('detail') or chain.get('status')}——"
                      "上台前必须复测")
    advice.append("演示前 30 分钟再跑一次本脚本；上台前 5 分钟不要重启服务")
    return advice


if __name__ == "__main__":
    raise SystemExit(main())
