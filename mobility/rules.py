"""规则引擎：解释语义层的「条件 → 判断 → 动作」，规则口径只有一处。"""
from __future__ import annotations
from .semantic import BusinessReject

OPS = {
    "eq":       lambda a, b: a == b,
    "ne":       lambda a, b: a != b,
    "gt":       lambda a, b: a is not None and b is not None and a > b,
    "gte":      lambda a, b: a is not None and b is not None and a >= b,
    "lt":       lambda a, b: a is not None and b is not None and a < b,
    "lte":      lambda a, b: a is not None and b is not None and a <= b,
    "in":       lambda a, b: a in b,
    "not_in":   lambda a, b: a not in b,
    "is_null":  lambda a, b: a is None,
    "not_null": lambda a, b: a is not None,
}


class RuleEngine:
    def __init__(self, semantic, actions: dict):
        self.sem = semantic
        self.actions = actions          # name -> callable(env) -> dict | None

    # ---------- 条件解释 ----------
    def _atom(self, atom: dict, ctx: dict) -> bool:
        left = ctx.get(atom["field"])
        right = ctx.get(atom["value_ref"]) if "value_ref" in atom else atom.get("value")
        fn = OPS.get(atom["op"])
        if fn is None:
            raise ValueError(f"不支持的操作符: {atom['op']}")
        # is_null / not_null 不需要 right
        if atom["op"] in ("is_null", "not_null"):
            return fn(left, None)
        return bool(fn(left, right))

    def _match(self, cond: dict, ctx: dict) -> bool:
        if "all" in cond:
            return all(self._match(x, ctx) for x in cond["all"])
        if "any" in cond:
            return any(self._match(x, ctx) for x in cond["any"])
        return self._atom(cond, ctx)

    # ---------- 费用计算（策略由语义层声明） ----------
    def fee(self, strategy: str | None, ctx: dict) -> float:
        if not strategy:
            return 0.0
        cfg = self.sem.pricing_cfg["cancellation"]
        wait = float(ctx.get("wait_minutes") or 0)
        over = max(0.0, wait - cfg["free_wait_minutes"])
        wait_fee = min(over * cfg["per_wait_minute"], cfg["wait_fee_cap"])
        reason = ctx.get("cancel_reason") or ""

        if strategy == "zero":
            return 0.0
        if strategy == "wait_time":
            return round(wait_fee, 2)
        if strategy == "actual_usage":
            p = self.sem.pricing(ctx["vehicle_type"])
            raw = p["base_fare"] + float(ctx.get("distance_km") or 0) * p["per_km"] \
                  + float(ctx.get("duration_min") or 0) * p["per_min"]
            total = max(p["min_fare"], raw) + self.sem.surcharge(ctx.get("region", "")) \
                    - self.sem.coupon_amount(ctx.get("coupon_id", "NONE"))
            return round(max(0.0, total), 2)
        if strategy == "arrived":
            waived = (cfg["driver_fault_waive"] and reason in cfg["driver_fault_reasons"]) or \
                     (cfg["force_majeure_waive"] and reason in cfg["force_majeure_reasons"])
            if waived:
                return 0.0
            return round(cfg["arrived_base_fee"] + wait_fee, 2)
        raise ValueError(f"未知的计费策略: {strategy}")

    # ---------- 执行：按 priority 依次匹配，动作可更新 ctx ----------
    def run_chain(self, domain: str, ctx: dict) -> dict:
        matched: list[dict] = []
        fee_value = 0.0
        last = None
        for rule in self.sem.rules(domain):
            if not self._match(rule["when"], ctx):
                continue
            judge = rule["judge"]
            if judge.get("fee_strategy"):
                fee_value = self.fee(judge["fee_strategy"], ctx)
            env = {
                "ctx": ctx, "rule": rule, "judge": judge, "fee": fee_value,
                "decision": judge["decision"], "explain": judge["explain"],
                "domain": domain,
            }
            for name in rule["actions"]:
                fn = self.actions.get(name)
                if fn is None:
                    raise ValueError(f"规则 {rule['id']} 引用了未注册动作: {name}")
                result = fn(env)
                if result:
                    ctx.update(result)
            matched.append(rule["id"])
            last = env
        if last is None:
            raise BusinessReject(f"{domain} 域没有匹配任何规则，请检查语义层定义")
        return {
            "matched_rules": matched,
            "rule_id": last["rule"]["id"],
            "rule_name": last["rule"]["name"],
            "decision": last["decision"],
            "explain": last["explain"],
            "fee": round(fee_value, 2),
            "fee_strategy": last["judge"].get("fee_strategy"),
            "ctx": ctx,
        }

    def explain_fee(self, strategy: str | None) -> str:
        if not strategy:
            return "本流程不产生费用"
        s = self.sem.fee_strategies.get(strategy, {})
        return f"{s.get('label','')}：{s.get('formula','')}"
