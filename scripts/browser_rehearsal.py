"""浏览器端到端彩排：6 主线讲解 + 每步真实执行断言 + 深挖展开。

自管理服务生命周期（lsof 精准清理 8765），运行：
    /tmp/pwenv/bin/python scripts/browser_rehearsal.py
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

import os
BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8765")
OUT = Path(__file__).parent / "_out"
# 沙箱环境下 os.mkdir 的 exist_ok 会被 broker 拦成 FileExistsError，
# 目录已存在时直接跳过即可（演示当天要反复跑，不能被这个挡住）
try:
    OUT.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass

ACTS = ["bearing", "machine", "contracts", "security", "agent", "evidence"]
EXPECT = {
    "bearing": ["承重", "ACT 1 / 6"],
    "machine": ["状态机", "ACT 2 / 6"],
    "contracts": ["来源契约", "ACT 3 / 6"],
    "security": ["权限", "ACT 4 / 6"],
    "agent": ["AI", "ACT 5 / 6"],
    "evidence": ["收束", "ACT 6 / 6"],
}

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def _run() -> int:
    with sync_playwright() as p:
        launch_kwargs = {"headless": True}
        if os.environ.get("PW_CHANNEL"):
            launch_kwargs["channel"] = os.environ["PW_CHANNEL"]
        elif os.environ.get("PW_EXECUTABLE"):
            launch_kwargs["executable_path"] = os.environ["PW_EXECUTABLE"]
        browser = p.chromium.launch(**launch_kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/profile/order-ontology", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        check("页面加载无 JS 错误", not errors, "; ".join(errors[:2]))

        # F-01：首屏只有 6 主线
        cards = page.locator("[data-comp-scenario]")
        check("首屏主线 6 题", cards.count() == 6, f"实际 {cards.count()} 题")

        start = page.locator('[data-demo="start"]')
        check("讲解模式入口可见", start.count() == 1 and start.is_visible())
        start.click()
        page.wait_for_timeout(500)

        for i, act in enumerate(ACTS):
            narration = page.locator(".demo-narration")
            text = narration.inner_text() if narration.count() else ""
            check(f"ACT{i+1} {act} 讲解词", all(n in text for n in EXPECT[act]),
                  text[:90].replace("\n", " "))

            run = page.locator('[data-comp-action="run"]')
            if run.count() and run.is_visible():
                run.click()
                try:
                    page.wait_for_selector(".comp-io .comp-io-tag.output", timeout=20000)
                    page.wait_for_timeout(500)
                    out = page.locator(".comp-io").last.inner_text()
                    ok = "这一步真实返回" in out and "执行失败" not in out and "ERROR" not in out
                    check(f"ACT{i+1} {act} 首步实时执行", ok, out.replace("\n", " ")[:120])
                except Exception as exc:
                    check(f"ACT{i+1} {act} 首步实时执行", False, str(exc)[:100])
            else:
                check(f"ACT{i+1} 首步执行", False, "找不到执行按钮")

            # F-09 声明依赖脚注
            dep = page.locator(".comp-dependency")
            check(f"ACT{i+1} 声明依赖脚注", dep.count() > 0 and "声明依赖" in dep.first.inner_text(),
                  dep.first.inner_text()[:90] if dep.count() else "无")

            page.screenshot(path=str(OUT / f"v2_act{i+1}_{act}.png"), full_page=False)

            if i < len(ACTS) - 1:
                nxt = page.locator('[data-demo="next"]')
                if nxt.count() and nxt.is_visible():
                    nxt.click()
                    page.wait_for_timeout(450)

        # 深挖展开
        toggle = page.locator('[data-comp-action="toggle-deep"]')
        if toggle.count():
            toggle.click()
            page.wait_for_timeout(400)
            check("深挖 4 题可展开", page.locator("[data-comp-scenario]").count() == 10,
                  f"展开后 {page.locator('[data-comp-scenario]').count()} 题")
        else:
            check("深挖 4 题可展开", False, "无展开按钮")

        # 区块 18：属性图对照实验（现场可跑）
        parity_btn = page.locator("#parityRunBtn")
        if parity_btn.count():
            parity_btn.scroll_into_view_if_needed()
            parity_btn.click()
            try:
                page.wait_for_selector("#parityResult table", timeout=20000)
                page.wait_for_timeout(300)
                body = page.locator("#parityResult").inner_text()
                check("区块18 对照实验可运行", "Q6" in body and "漏改实验" in body,
                      f"结果含 Q6 与漏改实验：{'是' if ('Q6' in body and '漏改实验' in body) else '否'}")
                check("区块18 漏改结论正确", "无任何机制能发现" in body and "漏改不可能发生" in body,
                      "属性图侧漏改不可发现 + 本体侧单源")
                check("区块18 对齐估算展示", "2968" in body,
                      f"完整对齐估算 2968 行{'可见' if '2968' in body else '缺失'}")
            except PlaywrightTimeoutError:
                check("区块18 对照实验可运行", False, "20 秒内未渲染出结果表")
        else:
            check("区块18 对照实验可运行", False, "未找到运行按钮")

        check("全程无 JS 错误", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\nRESULT {len(PASS)} passed / {len(FAIL)} failed")
    print(f"截图目录: {OUT}")
    return 0 if not FAIL else 1


def main() -> int:
    probe = socket.socket()
    if probe.connect_ex(("127.0.0.1", 8765)) == 0:
        probe.close()
        out = subprocess.run(["lsof", "-t", "-nP", "-iTCP:8765", "-sTCP:LISTEN"],
                             capture_output=True, text=True).stdout.split()
        for pid in out:
            try:
                os.kill(int(pid), 15)
            except (ProcessLookupError, ValueError):
                pass
        for _ in range(20):
            chk = socket.socket()
            free = chk.connect_ex(("127.0.0.1", 8765)) != 0
            chk.close()
            if free:
                break
            time.sleep(0.3)
    else:
        probe.close()

    srv = subprocess.Popen(["python3", "cli.py", "serve", "--port", "8765"],
                           cwd=str(Path(__file__).parent.parent),
                           stdout=open("/tmp/mobility_rehearsal_svc.log", "w"),
                           stderr=subprocess.STDOUT, start_new_session=True, env=os.environ)
    for _ in range(30):
        try:
            urllib_ok = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                        f"{BASE}/freight/health"],
                                       capture_output=True, text=True).stdout.strip() == "200"
            if urllib_ok:
                break
        except Exception:
            pass
        time.sleep(0.5)
    try:
        return _run()
    finally:
        try:
            os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())