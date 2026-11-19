"""P2-1 函数注册表：签名/版本/owner/测试用例入本体 JSON（对标 Palantir Functions 注册）。

调用路由：
- implementation 字符串 "module.path:callable" 或 "module.path.callable"
- accepts_interface 的函数实参先过接口 conforms 检查
- 未注册函数 -> FUNCTION_UNKNOWN（承重验收：删注册 -> 调用即失败）

安全联动：scope token 检查（SecurityEngine.check_scope("function", id)）。
"""
from __future__ import annotations

import importlib


class FunctionRegistryError(Exception):
    def __init__(self, message: str, code: str = "FUNCTION_ERROR"):
        super().__init__(message)
        self.code = code


class FunctionRegistry:
    def __init__(self, ext, security=None, interfaces=None):
        self.ext = ext
        self.security = security
        self.interfaces = interfaces

    def list_functions(self) -> list[dict]:
        return [
            {
                "id": f["id"], "label": f.get("label"),
                "version": f.get("version"), "owner": f.get("owner"),
                "returns": f.get("signature", {}).get("returns"),
                "accepts_interface": f.get("accepts_interface"),
            }
            for f in self.ext.function_registry.values()
        ]

    def resolve(self, function_id: str):
        fn = self.ext.function_registry.get(function_id)
        if fn is None:
            known = ", ".join(sorted(self.ext.function_registry)) or "无"
            raise FunctionRegistryError(
                f"函数 {function_id} 未注册（已注册: {known}）", code="FUNCTION_UNKNOWN"
            )
        impl = fn.get("implementation", "")
        try:
            module_path, _, attr = impl.rpartition(".")
            mod = importlib.import_module(module_path)
            callable_obj = getattr(mod, attr)
        except (ImportError, AttributeError) as e:
            raise FunctionRegistryError(
                f"函数 {function_id} 实现不可解析: {impl} ({e})", code="FUNCTION_IMPL_BROKEN"
            ) from e
        return fn, callable_obj

    def validate_signature(self, function_id: str, args: dict) -> list[str]:
        fn = self.ext.function_registry.get(function_id)
        if fn is None:
            return [f"函数 {function_id} 未注册"]
        problems = []
        for p in fn.get("signature", {}).get("params", []):
            if p.get("required") and p["name"] not in args:
                problems.append(f"缺少必填参数: {p['name']}")
        unknown = set(args) - {p["name"] for p in fn.get("signature", {}).get("params", [])}
        if unknown:
            problems.append(f"未知参数: {', '.join(sorted(unknown))}")
        return problems

    def call(self, function_id: str, args: dict, runtime=None, ctx=None) -> dict:
        # 1. scope token 越权检查（agent 调用先于一切）
        if ctx is not None and self.security is not None:
            self.security.check_scope(ctx, "function", function_id)
        # 2. 注册表路由
        fn, callable_obj = self.resolve(function_id)
        # 3. 签名校验
        problems = self.validate_signature(function_id, args)
        if problems:
            raise FunctionRegistryError("; ".join(problems), code="SIGNATURE_INVALID")
        # 4. 接口实参检查（waybill_id 类对象 conforms 声明接口）
        if self.interfaces is not None and runtime is not None:
            object_id = args.get("object_id") or args.get("waybill_id")
            if object_id:
                row = runtime.store.one("SELECT type FROM objects WHERE object_id=?", (object_id,))
                if row:
                    self.interfaces.check_function_arg(function_id, row["type"])
        # 5. 执行
        kwargs = dict(args)
        if callable_obj.__code__.co_varnames[:1] == ("system",) or "system" in (
            callable_obj.__code__.co_varnames[: callable_obj.__code__.co_argcount]
        ) and runtime is not None and "system" not in kwargs:
            kwargs["system"] = runtime
        try:
            result = callable_obj(**kwargs)
        except TypeError as e:
            raise FunctionRegistryError(
                f"函数 {function_id} 参数不匹配: {e}", code="ARGS_MISMATCH"
            ) from e
        return {
            "function": function_id,
            "version": fn.get("version"),
            "result": result,
        }
