"""浏览器端到端验证：规划驱动取证的四个新机制必须真实可见。

流程：打开页面 → 输入业务问题 → 一键跑到底（真实大模型推理链）
→ 展开全部步骤 → 断言新字段真实渲染：
  ① 规划步骤：取证重点带裁剪率 + 取证任务清单 + 待验证假设两张表
  ② 结论步骤：假设核对（裁定+证据）+ 计划执行对照 + 角色出口 + 未决项

运行（需本地/STG 服务已起）：
    /tmp/pwenv/bin/python scripts/_chain_browser_check.py [base_url]
"""
from __future__ import annotations

import sys
import time

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
QUESTION = "为什么这笔运单比预估贵 38 元？"

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []

        def _on_err(e):
            stack = getattr(e, "stack", "") or ""
            errors.append(f"{e}\n{stack}")

        page.on("pageerror", _on_err)
        page.goto(f"{BASE}/profile/order-ontology",
                  wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
        check("页面加载无 JS 错误", not errors, "; ".join(errors[:2]))

        page.click("#openChainHero")
        page.wait_for_selector("#chainQuestion", timeout=10000)
        page.fill("#chainQuestion", QUESTION)
        page.click("#chainAskGo")
        page.wait_for_selector("#chainRunAll", timeout=30000)
        page.click("#chainRunAll")
        print("… 会话已创建，开始一键跑到底（真实大模型，可能需数分钟）")

        started = time.time()
        while time.time() - started < 900:
            btn_text = page.inner_text("#chainRunAll")
            all_disabled = page.locator("#chainAll").is_disabled()
            if ("推理中" not in btn_text) and (not all_disabled):
                break
            if time.time() - started > 870:
                print(f"… 仍在推理：btn={btn_text!r} all_disabled={all_disabled}")
            page.wait_for_timeout(5000)
        print(f"… 推理结束，用时 {round(time.time() - started)}s；"
              f"chainRunAll={page.inner_text('#chainRunAll')!r}")
        body_now = page.inner_text(".modal")
        if page.locator("#chainAll").is_enabled():
            page.click("#chainAll")  # 展开全部步骤
        else:
            page.evaluate(
                "document.querySelector('.modal').classList.add('chain-show-all')")
        page.wait_for_timeout(1000)

        body = page.inner_text(".modal")
        check("推理链跑出结论", ("已完成" in body) or ("初步结论" in body),
              body[:120].replace("\n", " "))

        # ① 规划步骤：裁剪率 + 任务清单 + 假设表
        check("规划步骤展示取证重点裁剪率", "裁剪率" in body)
        check("规划步骤展示取证任务清单", "取证任务清单" in body)
        check("规划步骤展示待验证假设表", "待验证假设" in body)
        check("假设带可证伪判断列", "可证伪判断" in body)

        # ② 结论步骤收口三件套
        check("结论展示假设核对（裁定+证据）", "假设核对" in body)
        check("结论展示计划执行对照", "计划执行对照" in body)
        check("执行对照含状态", ("已执行" in body or "未执行" in body))
        check("结论展示角色出口", "角色出口" in body)
        check("结论展示未决项", "未决项" in body)

        # ③ 总览阶段表：执行轨迹台账（不是售前示意图）
        summary_txt = page.inner_text("#chainSummary")
        check("总览含六段骨架（动作出口入列）", "动作出口" in summary_txt)
        check("总览动作出口等人工确认", "等人工确认" in summary_txt)
        check("总览阶段表显示假设裁定", "假设裁定" in summary_txt)
        check("总览取证阶段显示循环计数", "次工具调用" in summary_txt
              and "次大模型决策" in summary_txt)
        check("总览规划阶段显示裁剪率", "裁剪率" in summary_txt)
        plan_txt = ""
        rows = page.locator(
            "#chainSummary .chain-summary-table .chain-summary-row")
        for i in range(rows.count()):
            txt = rows.nth(i).inner_text()
            if "链路规划" in txt:
                plan_txt = rows.nth(i).locator(".sum-action").inner_text()
                break
        check("总览阶段文案固定（规划行仍是取数计划）",
              "取数计划" in plan_txt and "复核" not in plan_txt,
              plan_txt[:40])
        part_title = page.locator(
            "#chainSummary .chain-summary-stats span").nth(2).get_attribute(
            "title") or ""
        check("总览参与方按真实步骤聚合", "Agent → 人工" in part_title,
              part_title[:60])

        check("全程无 JS 错误", not errors, " || ".join(errors[:2]))
        browser.close()

    print(f"\n结果：{len(PASS)} PASS / {len(FAIL)} FAIL")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
