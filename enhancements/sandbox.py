"""P2-2 函数沙箱：子进程隔离执行 + timeout + 内存限制（对标 Palantir 服务端隔离执行）。

只对注册表声明了 sandbox 配置的函数生效：
- timeout_ms：超时杀子进程，主进程存活（承重验收：死循环函数被杀）
- memory_mb：resource.RLIMIT_AS 限制
- 通过子进程 stdout 传 JSON 结果

纯 Python 计算函数（AST 解释的定价函数）不需要沙箱——沙箱面向
实现级函数（引用任意 import 的 Python callable）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import os

RUNNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sandbox_runner.py")


class SandboxTimeout(Exception):
    pass


class SandboxError(Exception):
    pass


def run_in_sandbox(function_id: str, args: dict, ext, timeout_ms: int = 5000,
                   memory_mb: int | None = 256) -> dict:
    """在隔离子进程中执行注册表函数，返回结果或抛超时。

    内存限制由子进程自设（RLIMIT_AS），避免依赖父进程 preexec_fn
    （部分受限环境不支持）。超时由父进程 subprocess timeout 兑现。
    """
    fn = ext.function_registry.get(function_id)
    if fn is None:
        raise SandboxError(f"函数 {function_id} 未注册")
    payload = {
        "function_id": function_id, "args": args, "memory_mb": memory_mb,
        "fn_definition": fn,  # 注册表定义随 payload 传递（支持运行时变异的本体）
    }
    cmd = [sys.executable, RUNNER, json.dumps(payload, ensure_ascii=False)]
    try:
        proc = subprocess.run(  # noqa: S603
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000.0,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
    except subprocess.TimeoutExpired as e:
        raise SandboxTimeout(
            f"函数 {function_id} 沙箱执行超过 {timeout_ms}ms 被终止"
        ) from e
    if proc.returncode != 0:
        raise SandboxError(
            f"函数 {function_id} 沙箱执行失败 (rc={proc.returncode}): {proc.stderr[-500:]}"
        )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise SandboxError(f"沙箱返回非 JSON: {proc.stdout[:200]}") from e
