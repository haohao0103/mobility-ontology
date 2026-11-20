"""P2-1 函数注册表：签名/版本/owner/测试用例入本体 JSON（对标 Palantir Functions 注册）。

调用路由：
- implementation 字符串 "module.path:callable" 或 "module.path.callable"
- accepts_interface 的函数实参先过接口 conforms 检查
- 未注册函数 -> FUNCTION_UNKNOWN（承重验收：删注册 -> 调用即失败）

安全联动：scope token 检查（SecurityEngine.check_scope("function", id)）。
"""
from __future__ import annotations

import importlib
import re


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

    @staticmethod
    def _load_impl(impl: str):
        """解析实现字符串，返回 (callable, needs_instance)。

        支持三种形态：
          module.function            模块级函数
          module:callable            冒号分隔（等价 module.function）
          module.Class.method        类方法（调用时注入运行时 system 实例为 self）
        """
        impl = impl.replace(":", ".")
        parts = impl.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            module_path = ".".join(parts[:cut])
            try:
                mod = importlib.import_module(module_path)
            except ImportError:
                continue
            try:
                obj = mod
                for name in parts[cut:]:
                    obj = getattr(obj, name)
            except AttributeError:
                continue
            # Class.method 形态：倒数第二层是类 -> 需要实例注入
            if len(parts) - cut >= 2:
                parent = mod
                for name in parts[cut:-1]:
                    parent = getattr(parent, name)
                if isinstance(parent, type):
                    return obj, True
            return obj, False
        raise ImportError(f"无法解析: {impl}")

    def resolve(self, function_id: str):
        fn = self.ext.function_registry.get(function_id)
        if fn is None:
            known = ", ".join(sorted(self.ext.function_registry)) or "无"
            raise FunctionRegistryError(
                f"函数 {function_id} 未注册（已注册: {known}）", code="FUNCTION_UNKNOWN"
            )
        impl = fn.get("implementation", "")
        try:
            callable_obj, needs_instance = self._load_impl(impl)
        except (ImportError, AttributeError) as e:
            raise FunctionRegistryError(
                f"函数 {function_id} 实现不可解析: {impl} ({e})", code="FUNCTION_IMPL_BROKEN"
            ) from e
        fn["_needs_instance"] = needs_instance
        return fn, callable_obj

    # 标量参数类型 -> 校验器。与 freight.semantic.SemanticLayer._type_ok 同一口径：
    # 类型判定分散成两套，迟早出现「属性入库拦得住、函数入参拦不住」的错位。
    DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")

    @classmethod
    def _scalar_ok(cls, ptype: str, value) -> bool:
        if value is None:
            return True
        if ptype in ("string", "text", "enum"):
            return isinstance(value, str)
        if ptype == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if ptype in ("number", "decimal"):
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if ptype == "boolean":
            return isinstance(value, bool)
        if ptype == "date":
            return isinstance(value, str) and bool(cls.DATE_RE.match(value))
        if ptype == "timestamp":
            return isinstance(value, str) and bool(cls.DATETIME_RE.match(value))
        if ptype == "json":
            return isinstance(value, (dict, list))
        # object_set / object_ref 由步骤 4 / object_ref 分支单独判定
        return True

    def validate_signature(self, function_id: str, args: dict) -> list[str]:
        fn = self.ext.function_registry.get(function_id)
        if fn is None:
            return [f"函数 {function_id} 未注册"]
        problems = []
        params = fn.get("signature", {}).get("params", [])
        for p in params:
            if p.get("required") and p["name"] not in args:
                problems.append(f"缺少必填参数: {p['name']}")
        unknown = set(args) - {p["name"] for p in params}
        if unknown:
            problems.append(f"未知参数: {', '.join(sorted(unknown))}")
        # 标量类型校验：只查「必填/未知」等于只校验了参数个数，类型错一个字节照样进函数。
        for p in params:
            name = p["name"]
            if name not in args:
                continue
            ptype = p.get("type")
            value = args[name]
            if ptype in ("object_set", "object_ref", None):
                continue
            if not self._scalar_ok(ptype, value):
                problems.append(
                    f"参数 {name} 类型不符：声明 {ptype}，"
                    f"实际 {type(value).__name__}（{value!r:.40}）")
        return problems

    # -------------------------------------------------- 返回值类型（Result Type）
    def return_type(self, function_id: str) -> dict | None:
        fn = self.ext.function_registry.get(function_id) or {}
        name = (fn.get("signature") or {}).get("returns")
        if not name:
            return None
        rt = self.ext.result_types.get(name)
        if rt is not None:
            return rt
        return None

    def check_return(self, function_id: str, result) -> None:
        """校验实际返回值与声明的 Result Type 一致。

        没有这一步，returns 就只是个注释：代码里的返回结构改了，注册表不知道，
        下游按旧结构解析，字段静默变 None。
        """
        fn = self.ext.function_registry.get(function_id) or {}
        declared = (fn.get("signature") or {}).get("returns")
        if not declared:
            return
        rt = self.ext.result_types.get(declared)
        if rt is None:
            # 标量返回（number/string/...）或对象类型 id
            if self._scalar_ok(declared, result) or declared in self.ext.base_classes:
                if declared in self.ext.base_classes and not isinstance(result, dict):
                    raise FunctionRegistryError(
                        f"函数 {function_id} 声明返回 {declared}（对象类型），"
                        f"实际返回 {type(result).__name__}", code="RETURN_TYPE_MISMATCH")
                return
            raise FunctionRegistryError(
                f"函数 {function_id} 的 returns={declared!r} 未在 result_types 注册，"
                f"也不是标量类型或对象类型", code="RETURN_TYPE_UNKNOWN")
        if not isinstance(result, dict):
            raise FunctionRegistryError(
                f"函数 {function_id} 声明返回 {declared}（结构化），"
                f"实际返回 {type(result).__name__}", code="RETURN_TYPE_MISMATCH")
        declared_fields = {f["name"]: f for f in rt["fields"]}
        missing = sorted(set(declared_fields) - set(result))
        if missing:
            raise FunctionRegistryError(
                f"函数 {function_id} 返回值缺少 {declared} 声明字段: {missing}",
                code="RETURN_FIELD_MISSING")
        extra = sorted(set(result) - set(declared_fields))
        if extra:
            raise FunctionRegistryError(
                f"函数 {function_id} 返回值含 {declared} 未声明字段: {extra}",
                code="RETURN_FIELD_UNDECLARED")
        for name, f in declared_fields.items():
            value = result[name]
            if value is None and f.get("nullable", True):
                continue
            if not self._scalar_ok(f["type"], value):
                raise FunctionRegistryError(
                    f"函数 {function_id} 返回字段 {name} 类型不符："
                    f"声明 {f['type']}，实际 {type(value).__name__}",
                    code="RETURN_FIELD_TYPE_MISMATCH")

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
        # 4. object_set 参数解析（Palantir Function 的 ObjectSet 入参形态）：
        #    查询树(dict) -> 过查询引擎（含行级安全）取对象 ID 列表；
        #    显式 ID 列表(list) -> 逐个查库校验类型。
        kwargs = dict(args)
        if runtime is not None:
            for p in fn.get("signature", {}).get("params", []):
                if p.get("type") != "object_set":
                    continue
                name = p["name"]
                val = kwargs.get(name)
                if val is None:
                    continue
                of_type = p.get("of")
                if isinstance(val, dict):
                    from freight.object_query import ObjectQueryEngine
                    engine = ObjectQueryEngine(
                        runtime.system.sem, runtime.ext, runtime.security)
                    out = engine.execute(val, runtime.store, ctx)
                    ids = [o["object_id"] for o in out.get("objects", [])
                           if of_type is None or o["type"] == of_type]
                    if of_type and len(ids) < out.get("total", 0):
                        raise FunctionRegistryError(
                            f"object_set 参数含非 {of_type} 类型对象",
                            code="SET_TYPE_MISMATCH")
                    kwargs[name] = ids
                elif isinstance(val, list):
                    ids = []
                    for i in val:
                        row = runtime.store.one(
                            "SELECT object_id, type FROM objects WHERE object_id=?",
                            (i,))
                        if row is None:
                            raise FunctionRegistryError(
                                f"object_set 参数含不存在对象: {i}",
                                code="OBJECT_NOT_FOUND")
                        if of_type and row["type"] != of_type:
                            raise FunctionRegistryError(
                                f"object_set 参数类型不符: {i} 是 {row['type']}，"
                                f"应为 {of_type}", code="SET_TYPE_MISMATCH")
                        ids.append(row["object_id"])
                    kwargs[name] = ids
                else:
                    raise FunctionRegistryError(
                        f"object_set 参数 {name} 必须是查询树(dict)或 ID 列表(list)",
                        code="SET_INVALID")
        # 5. 接口实参检查（waybill_id 类对象 conforms 声明接口）
        if self.interfaces is not None and runtime is not None:
            object_id = args.get("object_id") or args.get("waybill_id")
            if object_id:
                row = runtime.store.one("SELECT type FROM objects WHERE object_id=?", (object_id,))
                if row:
                    self.interfaces.check_function_arg(function_id, row["type"])
        # 6. 执行（kwargs 已在步骤 4 构造并可能被 object_set 解析改写）
        if callable_obj.__code__.co_varnames[:1] == ("system",) or "system" in (
            callable_obj.__code__.co_varnames[: callable_obj.__code__.co_argcount]
        ) and runtime is not None and "system" not in kwargs:
            kwargs["system"] = runtime
        try:
            if fn.get("_needs_instance") and runtime is not None:
                # Class.method 形态：注入运行时的 FreightSystem 实例为 self
                result = callable_obj(runtime.system, **kwargs)
            else:
                result = callable_obj(**kwargs)
        except TypeError as e:
            raise FunctionRegistryError(
                f"函数 {function_id} 参数不匹配: {e}", code="ARGS_MISMATCH"
            ) from e
        # 7. 返回值按 Result Type 校验（returns 不是注释，是契约）
        self.check_return(function_id, result)
        return {
            "function": function_id,
            "version": fn.get("version"),
            "result": result,
        }
