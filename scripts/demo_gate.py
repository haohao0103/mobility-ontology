#!/usr/bin/env python3
"""演示自检：上台前 5 分钟跑一遍，确认所有演示路径可用。

演示当天最贵的不是"系统没做好"，而是"现场才发现某条路径挂了"。
本脚本按演示动线逐项检查，并给出**耗时基线**（知道该等多久，心里不慌）。

用法：
    python scripts/demo_gate.py            # 全量自检（含一次真实 LLM 调用）
    python scripts/demo_gate.py --fast     # 跳过 LLM 推理链，查接口 + 全部负向翻转
    python scripts/demo_gate.py --fast --strict  # 任一负向翻转未命中即失败（上台前验收）
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
    ("对象状态轨迹", f"/freight/objects/{WAYBILL}/state-history", 10),
    ("费用差异诊断（三口径 + 来源覆盖）", f"/freight/waybills/{WAYBILL}/fee-analysis", 15),
    ("面向财务的解释", f"/freight/waybills/{WAYBILL}/explain?audience=finance", 15),
    ("对象图（两跳）", f"/freight/objects/{WAYBILL}/graph?depth=2", 10),
    ("证据链", "/freight/mcp/tools", 10),
    ("审计流水", "/freight/audit", 10),
    # 6 主线 + 4 深挖依赖的只读接口
    ("来源契约覆盖", "/freight/contracts/coverage", 10),
    ("ABox 覆盖率", "/freight/ontology/abox-coverage", 10),
    ("治理门禁（未签字）", "/freight/ontology/guard-demo?case=drop_property_unsigned", 10),
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


def _has_flip(payload: dict, code: str) -> bool:
    return any(isinstance(f, dict) and f.get("code") == code
               for f in (payload.get("flips") or []))


def _flip_check(code: str):
    def verify(payload: dict):
        hit = _has_flip(payload, code)
        return hit, f"flips {'命中' if hit else '缺失'} {code}"
    return verify


def _verify_action(payload: dict):
    cases = {c.get("case"): c for c in (payload.get("cases") or [])
             if isinstance(c, dict)}
    replayed = all(
        (cases.get(k, {}).get("result") or {}).get("replayed") is True
        for k in ("replay_1", "replay_2"))
    ok = (cases.get("wrong_role", {}).get("code") == "FORBIDDEN_ROLE"
          and cases.get("wrong_state", {}).get("ok") is False
          and cases.get("allowed", {}).get("ok") is True
          and replayed
          and payload.get("audit_delta") == 0
          and payload.get("state_restored") is True
          and payload.get("persisted") is False)
    return ok, (f"错角色={cases.get('wrong_role', {}).get('code')} "
                f"审计增量={payload.get('audit_delta')} "
                f"状态复原={payload.get('state_restored')}")


def _guard_check(expected: str):
    def verify(payload: dict):
        verdict = payload.get("verdict")
        unapproved = payload.get("unapproved_count")
        ok = (verdict == expected
              and (unapproved >= 1 if expected == "FAIL" else unapproved == 0)
              and (bool(payload.get("impacts")) if expected == "FAIL" else True)
              and payload.get("persisted") is False)
        return ok, f"verdict={verdict} 未审批={unapproved} impacts={len(payload.get('impacts') or [])}"
    return verify


def _verify_interface(payload: dict):
    before = payload.get("before") or {}
    after = payload.get("after") or {}
    ok = (before.get("impl_count", 0) > after.get("impl_count", 10 ** 9)
          and before.get("function_accepted") is True
          and after.get("function_accepted") is False
          and _has_flip(payload, "INTERFACE_VIOLATION")
          and payload.get("persisted") is False)
    return ok, (f"实现类 {before.get('impl_count')}→{after.get('impl_count')} "
                f"函数接受 {before.get('function_accepted')}→{after.get('function_accepted')}")


def _verify_guard_output(payload: dict):
    # 输出闸走 findings（不是 flips），与 enhancements.demo_labs.guard_output_lab 对齐
    codes = [f.get("code") for f in (payload.get("findings") or [])
             if isinstance(f, dict)]
    ok = payload.get("status") == "DENIED" and "OUTPUT_MARKING_DENIED" in codes
    return ok, f"输出闸={payload.get('status')} {codes}"


def _verify_marking(payload: dict):
    before = (payload.get("before") or {}).get("visible_rows", 0)
    after = (payload.get("after") or {}).get("visible_rows", 0)
    ok = after > before and bool(payload.get("flips")) and payload.get("persisted") is False
    return ok, f"可见行 {before}→{after}"


# 每条主线/深挖的负向翻转：改声明 → 结果翻转 → persisted:false
SCENARIOS = [
    ("M3 删认领→缺口出现", "POST", "/freight/contracts/simulate",
     {"case": "drop_claim"}, _flip_check("FEE_SOURCE_UNRESOLVED")),
    ("M2 动作门禁+幂等+回滚", "POST", "/freight/actions/lab", {}, _verify_action),
    ("D2 未签字门禁 FAIL", "GET", "/freight/ontology/guard-demo?case=drop_property_unsigned",
     None, _guard_check("FAIL")),
    ("D2 补签门禁 PASS", "GET", "/freight/ontology/guard-demo?case=drop_property_signed",
     None, _guard_check("PASS")),
    ("D4 改属性类型→拒绝", "POST", "/freight/ontology/type-lab",
     {"case": "waiting_minutes_boolean"}, _flip_check("TYPE_MISMATCH")),
    ("M5 抽工具→工具不可用", "POST", "/freight/agent/declaration-check",
     {"omit_tool": "ontology.catalog"}, _flip_check("TOOL_UNAVAILABLE")),
    ("M5 抽类型→类型不可圈定", "POST", "/freight/agent/declaration-check",
     {"omit_type": "FeeItem"}, _flip_check("TYPE_UNBOUND")),
    ("D3 删接口属性→多态拒绝", "POST", "/enh/interfaces/simulate",
     {"case": "drop_required_property"}, _verify_interface),
    ("M4 匿名输出被拒", "POST", "/enh/security/guard-output",
     {"user": "anonymous", "roles": ""}, _verify_guard_output),
    ("M4 删 marking→可见行翻转", "POST", "/enh/security/simulate-marking",
     {"case": "drop_source_marking", "class_id": "ExceptionTicket"}, _verify_marking),
]


def check_scenarios() -> list[dict]:
    rows = []
    for name, method, path, body, verify in SCENARIOS:
        t0 = time.time()
        status, payload = (_post(path, body) if method == "POST" else _get(path))
        elapsed = round(time.time() - t0, 2)
        try:
            ok, detail = verify(payload) if isinstance(payload, dict) else (False, "非 dict 响应")
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"断言异常 {type(exc).__name__}: {exc}"[:160]
        rows.append({"name": name, "path": path, "status": status, "ok": ok,
                     "elapsed_s": elapsed, "detail": detail,
                     "persisted": isinstance(payload, dict) and payload.get("persisted")})
    return rows


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
    agent = (result or {}).get("agent") or {}
    if isinstance(result, dict):
        # 结论在最后一步的 conclusion（不是 action.answer），顶层不直接给
        answer = str(result.get("final_answer") or result.get("answer") or "")
        if not answer:
            for step in reversed(result.get("steps") or []):
                if step.get("conclusion"):
                    answer = str(step["conclusion"])
                    break
    finished = bool(result.get("finished")) if isinstance(result, dict) else False
    error = agent.get("error") or ""
    return {
        "ok": status == 200 and finished and bool(agent.get("ok")) and not error and bool(answer),
        "status": status,
        "elapsed_s": elapsed,
        "auto_steps": (result or {}).get("auto_steps"),
        "model": (result or {}).get("model"),
        "answer_chars": len(answer),
        "participants": ((result or {}).get("summary") or {}).get("participants"),
        "detail": error,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过 LLM 推理链，保留全部负向翻转")
    ap.add_argument("--strict", action="store_true", help="任一负向翻转未命中即失败")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    started = time.time()
    endpoints = check_endpoints()
    scenarios = check_scenarios()
    llm = ({"ok": True, "skipped": True} if args.fast else check_llm())
    chain = {"ok": True, "skipped": True} if args.fast else check_chain(True)

    rows = list(endpoints)
    flips_ok = all(r["ok"] for r in scenarios)
    ok = (all(r["ok"] for r in rows) and llm.get("ok") and chain.get("ok")
          and (flips_ok or not args.strict))
    out = {
        "ok": ok,
        "base": BASE,
        "elapsed_s": round(time.time() - started, 2),
        "endpoints": rows,
        "scenarios": scenarios,
        "strict": args.strict,
        "flips_ok": flips_ok,
        "llm": llm,
        "chain": chain,
        "advice": _advice(rows, scenarios, llm, chain),
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
    print(f"  负向翻转（{len(scenarios)} 项{' · strict' if args.strict else ''}）：")
    for r in scenarios:
        mark = "OK " if r["ok"] else "FAIL"
        persisted = " / persisted=true(!)" if r.get("persisted") else ""
        print(f"  [{mark}] {r['name']:28s} {r['elapsed_s']:6.2f}s  "
              f"{r['detail']}{persisted}")
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


def _advice(rows, scenarios, llm, chain) -> list[str]:
    advice = []
    slow = [r["name"] for r in rows if r.get("slow")]
    if slow:
        advice.append(f"这些接口比预期慢：{', '.join(slow)}——演示前预热一次")
    failed = [r["name"] for r in scenarios if not r["ok"]]
    if failed:
        advice.append(f"负向翻转未命中：{', '.join(failed)}——按声明依赖逐项排查")
    polluted = [r["name"] for r in scenarios if r.get("persisted")]
    if polluted:
        advice.append(f"实验返回 persisted=true（写入主库）：{', '.join(polluted)}——立即停止演示")
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
