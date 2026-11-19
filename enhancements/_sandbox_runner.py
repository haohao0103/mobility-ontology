"""沙箱子进程执行器（内部文件，由 sandbox.run_in_sandbox 调起）。

职责单一：从 stdin argv 读 payload -> 加载注册表 -> 构造演示 runtime -> 调函数 -> stdout 回 JSON。
崩溃/超时由父进程兜底，子进程不做恢复。
"""
from __future__ import annotations

import json
import os
import resource
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from enhancements.function_registry import FunctionRegistry  # noqa: E402
from enhancements.registry import ExtensionRegistry  # noqa: E402
from freight.semantic import SemanticLayer  # noqa: E402


def _runtime():
    """沙箱内构造演示 runtime（默认事实库，只读用途）。"""
    try:
        from freight.engine import FreightSystem
        from enhancements.runtime import EnhancedSystem
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        db = os.path.join(root, "runs", "freight_facts.db")
        os.makedirs(os.path.dirname(db), exist_ok=True)
        return EnhancedSystem(FreightSystem(db, fresh=False, seed=True))
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    payload = json.loads(sys.argv[1])
    # 子进程自设内存限制（受限环境不支持父进程 preexec_fn）
    memory_mb = payload.get("memory_mb")
    if memory_mb:
        try:
            limit = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        except (ValueError, OSError):
            pass  # 限制失败不阻塞执行，timeout 仍然兜底
    sem = SemanticLayer()
    # 注册表定义随 payload 传入时（运行时变异的本体），写临时扩展文件加载
    fn_def = payload.get("fn_definition")
    if fn_def:
        import tempfile
        base = ExtensionRegistry(base_classes=sem.classes)
        raw = dict(base.raw)
        raw["function_registry"] = list(raw.get("function_registry", [])) + [fn_def]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as tf:
            json.dump(raw, tf, ensure_ascii=False)
            tmp = tf.name
        ext = ExtensionRegistry(path=tmp, base_classes=sem.classes)
    else:
        ext = ExtensionRegistry(base_classes=sem.classes)
    registry = FunctionRegistry(ext)
    runtime = _runtime()
    out = registry.call(payload["function_id"], payload["args"], runtime=runtime)
    json.dump(out, sys.stdout, ensure_ascii=False, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
