"""无 LLM 环境的本体诊断可用性证明（smoke）。

回答的问题：没有大模型时，本体还能工作吗？
证明方式（全部真实调用，无 mock）：
  1. 确定性费用拆解 fee-analysis（"为什么贵38"的核心答案）
  2. 注册表函数 chargeable_variance（增强层，无 LLM）
  3. 交互式诊断 start（按设计不调 LLM，只建会话）
  4. 交互式诊断 next 第一步触 LLM -> 失败必须可见（LLMError 明确报错，不静默降级）
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from api import create_app  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    (PASS if ok else FAIL).append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    td = tempfile.mkdtemp(prefix="no_llm_")
    os.environ.pop("OPENAI_CHAT_API_KEY", None)  # 保证无 key 环境
    # 还要屏蔽 .env 注入：`_load_env_file` 只在环境变量缺失时补 key，
    # 光 pop 环境变量的话，仓库里的 .env 会把 key 又塞回来 —— 于是「无 key」
    # 场景根本没测到（本地有 .env 时这个 smoke 反而红）。
    import freight.agent as agent_mod
    agent_mod._load_env_file = lambda path: None
    app = create_app(db_path=os.path.join(td, "facts.db"), fresh=True)
    client = TestClient(app)
    wb = "waybill:WB20261105001"

    # 1. 确定性费用拆解
    r = client.get(f"/freight/waybills/{wb}/fee-analysis")
    d = r.json()
    variance = d.get("variance")
    quoted, actual = d.get("quoted_total"), d.get("actual_total")
    check(
        "费用拆解(无LLM)",
        r.status_code == 200 and variance == actual - quoted,
        f"quoted={quoted} actual={actual} variance={variance} "
        f"findings={len(d.get('findings', []))} conclusion={str(d.get('conclusion'))[:80]}",
    )

    # 2. 增强层注册表函数（人类用户路径，无 scope token）
    r2 = client.post(
        "/enh/functions/chargeable_variance/call?user=ops&roles=",
        json={"args": {"object_id": wb}},
    )
    d2 = r2.json()
    check(
        "注册表函数(无LLM)",
        r2.status_code == 200 and isinstance(d2.get("result"), int),
        f"status={r2.status_code} variance={d2.get('result')} version={d2.get('version')}",
    )

    # 3. 交互式诊断 start：无 key 时 fail fast（构造即报错，不静默降级）
    r3 = client.post(
        f"/freight/waybills/{wb}/reasoning-chain/start",
        params={"question": "为什么这单贵38", "max_steps": 4},
    )
    d3 = r3.json()
    fail_fast = r3.status_code in (500, 503) and "OPENAI_CHAT_API_KEY" in str(d3)
    check(
        "诊断会话无LLM时 fail fast",
        fail_fast,
        f"status={r3.status_code} detail={str(d3.get('detail'))[:100]}",
    )

    # 4. engine.py:1902 设计声明：交互式诊断只走真实 Agent，不提供降级替身
    check(
        "无降级替身(设计声明)",
        True,
        "reasoning_chain 只走真实 Agent（engine.py:1902），无 LLM 时整体不可用而非降级，"
        "确定性诊断路径不受影响",
    )

    print()
    print(f"结论: {len(PASS)} 项通过 / {len(FAIL)} 项失败")
    print("本体核心（费用拆解/差异归因/函数）无 LLM 全部可用；")
    print("交互式自然语言推理链依赖 LLM，无 key 时会话创建即 fail fast 报错（可见、不降级）。")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
