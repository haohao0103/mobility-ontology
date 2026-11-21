"""浏览器端到端彩排：真实点击新 8 幕讲解模式 + 每幕首步实时执行断言。

运行（需先启动本地 8765 服务）：
    /tmp/pwenv/bin/python scripts/browser_rehearsal.py
产出：scripts/_out/rehearsal_act*.png
"""
from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

import os
BASE = os.environ.get("DEMO_BASE", "http://127.0.0.1:8765")
OUT = Path(__file__).parent / "_out"
OUT.mkdir(exist_ok=True)

ACTS = [
    ("bearing", "承重"), ("lineage", "血缘"), ("governance", "治理"),
    ("query", "语义查询"), ("types", "类型"), ("agent", "推理链"),
    ("security", "权限"), ("evidence", "收束"),
]
EXPECT = {
    "bearing": ["免费等候", "ACT 1 / 8"],
    "lineage": ["4097", "ACT 2 / 8"],
    "governance": ["Git", "ACT 3 / 8"],
    "query": ["业务语义", "ACT 4 / 8"],
    "types": ["契约", "ACT 5 / 8"],
    "agent": ["大模型", "ACT 6 / 8"],
    "security": ["客服看不到", "ACT 7 / 8"],
    "evidence": ["38", "ACT 8 / 8"],
}

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    import os
    import signal
    import subprocess
    import urllib.request

    import socket
    probe = socket.socket()
    occupied = probe.connect_ex(("127.0.0.1", 8765)) == 0
    probe.close()
    if occupied:
        # 只杀真正持有 8765 的 PID，不用字符串匹配以免误伤
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

    srv = subprocess.Popen(
        ["python3", "cli.py", "serve", "--port", "8765"],
        cwd=str(Path(__file__).parent.parent),
        stdout=open("/tmp/mobility_rehearsal_svc.log", "w"),
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=os.environ,
    )
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{BASE}/freight/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("服务启动失败")
        return 1

    try:
        return _run()
    finally:
        try:
            os.killpg(os.getpgid(srv.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass


def _run() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{BASE}/profile/order-ontology", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        check("页面加载无 JS 错误", not errors, "; ".join(errors[:2]))

        start = page.locator('[data-demo="start"]')
        check("讲解模式入口可见", start.count() == 1 and start.is_visible())
        start.click()
        page.wait_for_timeout(600)

        for i, (act, label) in enumerate(ACTS):
            narration = page.locator(".demo-narration")
            text = narration.inner_text() if narration.count() else ""
            ok = all(n in text for n in EXPECT[act])
            check(f"ACT{i+1} {act} 讲解词", ok, text[:100].replace("\n", " "))

            # 执行本幕第一步，断言真实 OUTPUT 且不是错误
            run = page.locator('[data-comp-action="run"]')
            if run.count() and run.is_visible():
                run.click()
                try:
                    page.wait_for_selector(".comp-io .comp-io-tag.output", timeout=20000)
                    page.wait_for_timeout(500)
                    out = page.locator(".comp-io").last.inner_text()
                    ok = "这一步真实返回" in out and "执行失败" not in out and "ERROR" not in out
                    check(f"ACT{i+1} 首步实时执行", ok, out.replace("\n", " ")[:130])
                except Exception as exc:
                    check(f"ACT{i+1} 首步实时执行", False, str(exc)[:100])
            else:
                check(f"ACT{i+1} 首步实时执行", False, "找不到执行按钮")

            page.screenshot(path=str(OUT / f"rehearsal_act{i+1}_{act}.png"), full_page=False)

            if i < len(ACTS) - 1:
                nxt = page.locator('[data-demo="next"]')
                if nxt.count() and nxt.is_visible():
                    nxt.click()
                    page.wait_for_timeout(500)
                else:
                    check(f"ACT{i+1}→{i+2} 切换", False, "按钮不可见")

        check("全程无 JS 错误", not errors, "; ".join(errors[:3]))
        browser.close()

    print(f"\nRESULT {len(PASS)} passed / {len(FAIL)} failed")
    print(f"截图目录: {OUT}")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
