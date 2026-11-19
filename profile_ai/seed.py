"""画像语义层演示资产：8 类顶点 + 10 类时态边。

所有写入先经过 ProfileSemanticLayer 校验，再落到 HugeGraph。
"""
from __future__ import annotations

from typing import Any, Dict, List

from .hugegraph import HugeGraphError
from .semantic import ProfileSemanticLayer, has_errors


def _clean(props: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None and v != ""}


class ProfileSeeder:
    def __init__(self, service):
        self.service = service
        self.semantic: ProfileSemanticLayer = service.semantic
        self.client = service.client
        self.written = {"vertices": 0, "edges": 0}
        self.violations: List[dict] = []

    def vertex(self, label: str, asset_id: str, props: dict) -> dict:
        props = _clean(props)
        violations = self.semantic.validate_vertex(label, props, asset_id)
        if has_errors(violations):
            self.violations.extend(violations)
            raise ValueError(f"顶点校验失败 {asset_id}: {violations}")
        self.client.upsert_vertex(label, asset_id, props)
        self.written["vertices"] += 1
        return props

    def edge(self, predicate: str, source_class: str, source_id: str,
             target_class: str, target_id: str, props: dict) -> None:
        props = _clean(props)
        violations = self.semantic.validate_relation(
            predicate, source_class, target_class, props, source_id, target_id)
        if has_errors(violations):
            self.violations.extend(violations)
            raise ValueError(f"边校验失败 {predicate}: {violations}")
        label = self.semantic.edge_label(predicate, source_class)
        try:
            self.client.add_edge(label, source_id, target_id, props)
        except HugeGraphError as e:
            # 时态边以 valid_from 作为排序键，重复 seed 时同一条边会已存在。
            msg = str(e).lower()
            if e.status in (400, 409) and ("exist" in msg or "已存在" in msg):
                return
            raise
        self.written["edges"] += 1

    def run(self) -> dict:
        ts = "2026-03-01T00:00:00Z"
        vf = "2026-01-01T00:00:00Z"

        # 36 个托管城市：规则故意只写前 30 个，用于演示“漏 6 城”。
        managed_cities = [
            ("110000", "北京"), ("310000", "上海"), ("440100", "广州"),
            ("440300", "深圳"), ("510100", "成都"), ("330100", "杭州"),
            ("500000", "重庆"), ("420100", "武汉"), ("610100", "西安"),
            ("320500", "苏州"), ("120000", "天津"), ("320100", "南京"),
            ("410100", "郑州"), ("430100", "长沙"), ("441900", "东莞"),
            ("210100", "沈阳"), ("370200", "青岛"), ("340100", "合肥"),
            ("440600", "佛山"), ("330200", "宁波"), ("530100", "昆明"),
            ("350100", "福州"), ("320200", "无锡"), ("350200", "厦门"),
            ("230100", "哈尔滨"), ("220100", "长春"), ("360100", "南昌"),
            ("370100", "济南"), ("210200", "大连"), ("520100", "贵阳"),
            ("450100", "南宁"), ("130100", "石家庄"), ("140100", "太原"),
            ("650100", "乌鲁木齐"), ("620100", "兰州"), ("460100", "海口"),
        ]
        all_codes = [code for code, _ in managed_cities]
        partial_codes = all_codes[:30]
        missing_codes = all_codes[30:]

        def rule(codes, vehicle=None, sticker=None):
            parts = []
            if vehicle:
                parts.append(f"driver.vehicle_type = '{vehicle}'")
            if sticker:
                parts.append(f"driver.sticker_status = '{sticker}'")
            parts.append("driver.city_code in [" +
                         ", ".join(f"'{c}'" for c in codes) + "]")
            return " and ".join(parts)

        operators = {
            "operator:internal:alice": {
                "name": "alice", "display_name": "Alice 运营",
                "role": "growth_operator", "account": "alice",
                "email": "alice@example.com", "status": "ACTIVE",
                "source_system": "internal", "source_id": "alice",
                "created_at": ts, "updated_at": ts,
            },
            "operator:internal:bob": {
                "name": "bob", "display_name": "Bob 数据",
                "role": "data_steward", "account": "bob",
                "email": "bob@example.com", "status": "ACTIVE",
                "source_system": "internal", "source_id": "bob",
                "created_at": ts, "updated_at": ts,
            },
        }
        tags = {
            "tag:crm:commute_rider": {
                "name": "通勤骑手", "display_name": "通勤骑手",
                "description": "工作日早晚高峰有稳定出行行为的用户",
                "status": "ACTIVE", "owner": "alice", "domain": "mobility",
                "source_system": "crm", "source_id": "commute_rider",
                "definition": "最近 30 天工作日 7-10 点或 17-20 点有 >= 8 次订单",
                "expression": "count(order where weekday and peak_hour) >= 8",
                "value_type": "BOOLEAN", "keywords": ["通勤", "高峰", "高频"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:coupon_sensitive": {
                "name": "优惠敏感", "display_name": "优惠敏感",
                "description": "对折扣券核销率显著高于均值",
                "status": "ACTIVE", "owner": "alice", "domain": "marketing",
                "source_system": "crm", "source_id": "coupon_sensitive",
                "definition": "近 90 天优惠券核销率 >= 35%",
                "expression": "coupon_redeem_rate_90d >= 0.35",
                "value_type": "BOOLEAN", "keywords": ["优惠", "券", "价格"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:high_value": {
                "name": "高价值", "display_name": "高价值用户",
                "description": "近 90 天贡献 GMV 排名前 20%",
                "status": "ACTIVE", "owner": "bob", "domain": "value",
                "source_system": "crm", "source_id": "high_value",
                "definition": "gmv_90d >= p80(gmv_90d)",
                "expression": "gmv_90d >= percentile(gmv_90d, 0.8)",
                "value_type": "BOOLEAN", "keywords": ["高价值", "GMV"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:churn_risk": {
                "name": "流失风险", "display_name": "流失风险",
                "description": "近 30 天活跃度下降且无下单",
                "status": "ACTIVE", "owner": "bob", "domain": "retention",
                "source_system": "crm", "source_id": "churn_risk",
                "definition": "last_order_days > 21 且 active_days_7d < 2",
                "expression": "last_order_days > 21 and active_days_7d < 2",
                "value_type": "BOOLEAN", "keywords": ["流失", "召回"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:small_vehicle_driver": {
                "name": "小车司机", "display_name": "小车司机",
                "description": "当前车辆类型为小型车的司机",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "crm", "source_id": "small_vehicle_driver",
                "definition": "vehicle_type = SMALL",
                "expression": "vehicle_type == 'SMALL'",
                "value_type": "BOOLEAN", "keywords": ["小车", "车型"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:large_vehicle_driver": {
                "name": "大车司机", "display_name": "大车司机",
                "description": "当前车辆类型为大型车的司机",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "crm", "source_id": "large_vehicle_driver",
                "definition": "vehicle_type = LARGE",
                "expression": "vehicle_type == 'LARGE'",
                "value_type": "BOOLEAN", "keywords": ["大车", "车型"],
                "created_at": ts, "updated_at": ts,
            },
            "tag:crm:sticker_active": {
                "name": "车贴已贴", "display_name": "车贴已贴",
                "description": "司机车贴状态为已张贴",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "crm", "source_id": "sticker_active",
                "definition": "sticker_status = ACTIVE",
                "expression": "sticker_status == 'ACTIVE'",
                "value_type": "BOOLEAN", "keywords": ["车贴", "已贴"],
                "created_at": ts, "updated_at": ts,
            },
        }
        crowds = {
            "crowd:crm:new_user_7d": {
                "name": "7 日新客", "display_name": "7 日新客",
                "description": "注册 7 天内且完成首单的新用户",
                "status": "ACTIVE", "owner": "alice", "domain": "growth",
                "source_system": "crm", "source_id": "new_user_7d",
                "keywords": ["新客", "首单"], "estimated_size": 128000,
                "target_size": 100000, "priority": 1,
                "permission_scope": "growth_team", "created_at": ts, "updated_at": ts,
            },
            "crowd:crm:high_value_commuters": {
                "name": "高价值通勤人群",
                "display_name": "高价值通勤人群",
                "description": "通勤骑手中的高价值用户，用于春季优惠券策略",
                "status": "ACTIVE", "owner": "alice", "domain": "growth",
                "source_system": "crm", "source_id": "high_value_commuters",
                "keywords": ["高价值", "通勤", "优惠券"], "estimated_size": 86000,
                "target_size": 86000, "priority": 1,
                "permission_scope": "growth_team", "created_at": ts, "updated_at": ts,
            },
            # 主案例：名称/描述说“大车 + 36 城 + 车贴已贴”，
            # 但规则写成 SMALL 且只圈了 30 城，用于演示结构化意图定罪。
            "crowd:crm:large_driver_sticker_36": {
                "name": "大车司机 36 城车贴激励",
                "display_name": "大车司机 36 城车贴激励",
                "description": "面向 36 个托管城市的大车司机发放车贴激励，要求车贴已贴",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "crm", "source_id": "large_driver_sticker_36",
                "keywords": ["大车", "车贴", "36 城"],
                "rule_expression": rule(partial_codes, "SMALL", "ACTIVE"),
                "rule_language": "dsl",
                "referenced_tags": ["tag:crm:small_vehicle_driver",
                                    "tag:crm:sticker_active"],
                "scope": partial_codes, "crowd_type": "vehicle_segment",
                "expected_city_count": 36,
                "intent_vehicle_type": "LARGE",
                "intent_sticker_status": "ACTIVE",
                "intent_expected_city_count": 36,
                "intent_managed_cities": ["ALL"],
                "estimated_size": 24000, "target_size": 24000, "priority": 1,
                "permission_scope": "fleet_team", "created_at": ts, "updated_at": ts,
            },
            "crowd:crm:large_driver_36_ok": {
                "name": "36 城托管大车司机（正确样例）",
                "display_name": "36 城托管大车司机（正确样例）",
                "description": "覆盖 36 个托管城市的大车司机，且车贴已贴",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "crm", "source_id": "large_driver_36_ok",
                "keywords": ["大车", "车贴", "36 城"],
                "rule_expression": rule(all_codes, "LARGE", "ACTIVE"),
                "rule_language": "dsl",
                "referenced_tags": ["tag:crm:large_vehicle_driver",
                                    "tag:crm:sticker_active"],
                "scope": all_codes, "crowd_type": "vehicle_segment",
                "expected_city_count": 36,
                "intent_vehicle_type": "LARGE",
                "intent_sticker_status": "ACTIVE",
                "intent_expected_city_count": 36,
                "intent_managed_cities": ["ALL"],
                "estimated_size": 22600, "target_size": 22600, "priority": 2,
                "permission_scope": "fleet_team", "created_at": ts, "updated_at": ts,
            },
            "crowd:crm:sticker_none_recruit": {
                "name": "车贴未贴司机招募",
                "display_name": "车贴未贴司机招募",
                "description": "招募尚未粘贴车贴的司机，完成粘贴后发放奖励",
                "status": "ACTIVE", "owner": "bob", "domain": "fleet",
                "source_system": "crm", "source_id": "sticker_none_recruit",
                "keywords": ["车贴", "未贴", "招募"],
                "rule_expression": rule(partial_codes[:10], None, "ACTIVE"),
                "rule_language": "dsl",
                "referenced_tags": ["tag:crm:sticker_active"],
                "scope": partial_codes[:10], "crowd_type": "recruit",
                "intent_sticker_status": "NONE",
                "estimated_size": 8200, "target_size": 8200, "priority": 2,
                "permission_scope": "fleet_team", "created_at": ts, "updated_at": ts,
            },
        }
        strategies = {
            "strategy:marketing:spring_coupon": {
                "name": "春季通勤优惠券", "display_name": "春季通勤优惠券",
                "description": "向高价值通勤人群发放 8 折通勤券",
                "status": "ACTIVE", "owner": "alice", "domain": "marketing",
                "source_system": "marketing", "source_id": "spring_coupon",
                "rule": "crowd = high_value_commuters and coupon_sensitive",
                "action": "ISSUE_COUPON", "trigger_type": "SCHEDULED",
                "channel": "APP_PUSH", "risk_level": "MEDIUM", "priority": 2,
                "effective_from": vf, "effective_to": "2026-06-30T23:59:59Z",
                "created_at": ts, "updated_at": ts,
            },
            "strategy:fleet:sticker_incentive": {
                "name": "大车车贴激励", "display_name": "大车车贴激励",
                "description": "向 36 城大车司机发放车贴激励，驱动车贴覆盖",
                "status": "ACTIVE", "owner": "alice", "domain": "fleet",
                "source_system": "marketing", "source_id": "sticker_incentive",
                "rule": "crowd = large_driver_sticker_36",
                "action": "ISSUE_REWARD", "trigger_type": "SCHEDULED",
                "channel": "APP_PUSH", "risk_level": "HIGH", "priority": 1,
                "effective_from": vf, "effective_to": "2026-12-31T23:59:59Z",
                "created_at": ts, "updated_at": ts,
            },
        }
        canvases = {
            "canvas:marketing:spring_push": {
                "name": "春季通勤券画布", "display_name": "春季通勤券画布",
                "description": "人群圈选 -> 策略绑定 -> App Push 发布",
                "status": "RUNNING", "owner": "alice", "domain": "marketing",
                "source_system": "marketing", "source_id": "spring_push",
                "canvas_id": "canvas_spring_2026", "canvas_name": "春季增长画布",
                "task_type": "AUDIENCE_PUBLISH", "schedule": "0 9 * * *",
                "audience_rule": "high_value_commuters",
                "publish_channel": "APP_PUSH",
                "last_run_at": "2026-03-01T09:00:00Z",
                "next_run_at": "2026-03-02T09:00:00Z",
                "result": "SUCCESS", "created_at": ts, "updated_at": ts,
            },
            "canvas:fleet:sticker_push": {
                "name": "大车车贴激励画布", "display_name": "大车车贴激励画布",
                "description": "36 城大车人群 -> 车贴激励策略 -> App Push 发布",
                "status": "RUNNING", "owner": "alice", "domain": "fleet",
                "source_system": "marketing", "source_id": "sticker_push",
                "canvas_id": "canvas_sticker_2026", "canvas_name": "车贴增长画布",
                "task_type": "AUDIENCE_PUBLISH", "schedule": "0 9 * * *",
                "audience_rule": "large_driver_sticker_36",
                "publish_channel": "APP_PUSH",
                "last_run_at": "2026-03-01T09:00:00Z",
                "next_run_at": "2026-03-02T09:00:00Z",
                "result": "SUCCESS", "created_at": ts, "updated_at": ts,
            },
        }
        orders = {
            "order:ops:o001": {
                "name": "订单 o001", "status": "COMPLETED",
                "source_system": "ops", "source_id": "o001", "order_id": "o001",
                "driver_id": "driver:crm:d001", "city_code": "110000",
                "amount": 128.5, "created_at": ts, "updated_at": ts,
            },
            "order:ops:o002": {
                "name": "订单 o002", "status": "COMPLETED",
                "source_system": "ops", "source_id": "o002", "order_id": "o002",
                "driver_id": "driver:crm:d002", "city_code": "310000",
                "amount": 76.0, "created_at": ts, "updated_at": ts,
            },
            "order:ops:o003": {
                "name": "订单 o003", "status": "COMPLETED",
                "source_system": "ops", "source_id": "o003", "order_id": "o003",
                "driver_id": "driver:crm:d003", "city_code": "440100",
                "amount": 210.0, "created_at": ts, "updated_at": ts,
            },
        }
        metrics = {
            "metric:bi:gmv_lift": {
                "name": "GMV 提升率", "display_name": "GMV 提升率",
                "description": "实验组相对对照组的 GMV 提升",
                "status": "ACTIVE", "owner": "bob", "domain": "value",
                "source_system": "bi", "source_id": "gmv_lift",
                "metric_id": "gmv_lift", "metric_name": "gmv_lift",
                "value": 0.126, "value_type": "RATIO", "unit": "%",
                "period": "2026-02", "sample_size": 86000, "direction": "UP",
                "definition": "(gmv_treatment - gmv_control) / gmv_control",
                "expression": "gmv_treatment / gmv_control - 1",
                "created_at": ts, "updated_at": ts,
            },
            "metric:bi:retention_7d": {
                "name": "7 日留存率", "display_name": "7 日留存率",
                "description": "触达人群 7 日后仍活跃的比例",
                "status": "ACTIVE", "owner": "bob", "domain": "retention",
                "source_system": "bi", "source_id": "retention_7d",
                "metric_id": "retention_7d", "metric_name": "retention_7d",
                "value": 0.418, "value_type": "RATIO", "unit": "%",
                "period": "2026-02", "sample_size": 86000, "direction": "UP",
                "definition": "active_d7 / cohort_size",
                "expression": "active_d7 / cohort_size",
                "created_at": ts, "updated_at": ts,
            },
        }
        evidence = {
            "evidence:warehouse:seg_2026_03": {
                "name": "人群规模校验 2026-03",
                "description": "数仓侧对高价值通勤人群的规模与覆盖率校验",
                "source_system": "warehouse", "source_id": "seg_2026_03",
                "source_url": "hive://dws.profile_segment/seg_2026_03",
                "evidence_type": "DATA_PROFILE", "content": "规模 86,000，覆盖率 92.4%，标签完整率 99.1%",
                "observed_at": "2026-03-01T02:00:00Z", "confidence": 0.98,
                "sample_size": 86000, "created_at": ts, "updated_at": ts,
            },
            "evidence:bi:abtest_042": {
                "name": "春季券 A/B 实验",
                "description": "春季通勤优惠券实验组与对照组效果",
                "source_system": "bi", "source_id": "abtest_042",
                "source_url": "bi://experiment/abtest_042",
                "evidence_type": "AB_TEST", "content": "GMV +12.6%，7 日留存 +3.2pp，统计显著 p<0.01",
                "observed_at": "2026-03-05T10:00:00Z", "confidence": 0.96,
                "sample_size": 86000, "created_at": ts, "updated_at": ts,
            },
            "evidence:metadata:tag_audit": {
                "name": "标签口径审计",
                "description": "标签定义与 SQL 口径一致性审计",
                "source_system": "metadata", "source_id": "tag_audit",
                "evidence_type": "AUDIT", "content": "通勤骑手与优惠敏感标签口径一致，无冲突",
                "observed_at": "2026-02-20T08:30:00Z", "confidence": 0.93,
                "sample_size": 4, "created_at": ts, "updated_at": ts,
            },
        }
        versions = {
            "version:crm:new_user_7d:v1": {
                "name": "7 日新客 v1", "description": "首版人群定义",
                "version": "v1", "status": "ACTIVE",
                "change_summary": "创建 7 日新客人群",
                "source_system": "crm", "source_id": "new_user_7d:v1",
                "created_at": "2026-01-15T08:00:00Z",
                "updated_at": "2026-01-15T08:00:00Z",
            },
            "version:crm:new_user_7d:v2": {
                "name": "7 日新客 v2", "description": "补充首单完成条件",
                "version": "v2", "previous_version": "v1", "status": "ACTIVE",
                "change_summary": "增加“完成首单”条件，排除注册未下单用户",
                "source_system": "crm", "source_id": "new_user_7d:v2",
                "created_at": "2026-02-10T08:00:00Z",
                "updated_at": "2026-02-10T08:00:00Z",
            },
            "version:crm:high_value_commuters:v1": {
                "name": "高价值通勤人群 v1", "description": "首版人群定义",
                "version": "v1", "status": "ACTIVE",
                "change_summary": "组合通勤骑手与高价值标签",
                "source_system": "crm", "source_id": "high_value_commuters:v1",
                "created_at": "2026-02-15T08:00:00Z",
                "updated_at": "2026-02-15T08:00:00Z",
            },
            "version:crm:large_driver_sticker_36:v1": {
                "name": "大车司机 36 城车贴激励 v1",
                "description": "首版人群定义，规则误写为 SMALL 且漏 6 城",
                "version": "v1", "status": "ACTIVE",
                "change_summary": "创建 36 城大车车贴人群",
                "source_system": "crm",
                "source_id": "large_driver_sticker_36:v1",
                "created_at": "2026-02-20T08:00:00Z",
                "updated_at": "2026-02-20T08:00:00Z",
            },
        }

        for asset_id, props in operators.items():
            self.vertex("Operator", asset_id, props)
        for code, name in managed_cities:
            self.vertex("City", f"city:ops:{code}", {
                "name": name, "display_name": name, "city_name": name,
                "city_code": code, "managed": True, "status": "ACTIVE",
                "owner": "ops", "domain": "fleet", "source_system": "ops",
                "source_id": code, "created_at": ts, "updated_at": ts,
            })
        for asset_id, props in tags.items():
            self.vertex("Tag", asset_id, props)
        for asset_id, props in crowds.items():
            self.vertex("Crowd", asset_id, props)
        for asset_id, props in strategies.items():
            self.vertex("Strategy", asset_id, props)
        for asset_id, props in canvases.items():
            self.vertex("CanvasTask", asset_id, props)
        for asset_id, props in orders.items():
            self.vertex("Order", asset_id, props)
        for asset_id, props in metrics.items():
            self.vertex("Metric", asset_id, props)
        for asset_id, props in evidence.items():
            self.vertex("Evidence", asset_id, props)
        for asset_id, props in versions.items():
            self.vertex("Version", asset_id, props)

        edge_props = {
            "valid_from": vf, "source_system": "profile_ai", "created_at": ts,
        }

        # 人群 -> 标签
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:new_user_7d",
                  "Tag", "tag:crm:commute_rider", {**edge_props, "weight": 0.8})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:new_user_7d",
                  "Tag", "tag:crm:churn_risk", {**edge_props, "weight": 0.6})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:high_value_commuters",
                  "Tag", "tag:crm:commute_rider", {**edge_props, "weight": 0.9})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:high_value_commuters",
                  "Tag", "tag:crm:high_value", {**edge_props, "weight": 0.95})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:high_value_commuters",
                  "Tag", "tag:crm:coupon_sensitive", {**edge_props, "weight": 0.7})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:large_driver_sticker_36",
                  "Tag", "tag:crm:small_vehicle_driver", {**edge_props, "weight": 0.9})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:large_driver_sticker_36",
                  "Tag", "tag:crm:sticker_active", {**edge_props, "weight": 0.9})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:large_driver_36_ok",
                  "Tag", "tag:crm:large_vehicle_driver", {**edge_props, "weight": 0.9})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:large_driver_36_ok",
                  "Tag", "tag:crm:sticker_active", {**edge_props, "weight": 0.9})
        self.edge("crowd_has_tag", "Crowd", "crowd:crm:sticker_none_recruit",
                  "Tag", "tag:crm:sticker_active", {**edge_props, "weight": 0.8})

        # 人群 -> 城市（主案例故意只圈 30 个托管城市）
        scope_by_crowd = {
            "crowd:crm:large_driver_sticker_36": partial_codes,
            "crowd:crm:large_driver_36_ok": all_codes,
            "crowd:crm:sticker_none_recruit": partial_codes[:10],
        }
        for crowd_id, codes in scope_by_crowd.items():
            for code in codes:
                self.edge("crowd_scope_city", "Crowd", crowd_id,
                          "City", f"city:ops:{code}", edge_props)

        # 人群派生
        self.edge("crowd_derived_from", "Crowd",
                  "crowd:crm:high_value_commuters", "Crowd",
                  "crowd:crm:new_user_7d",
                  {**edge_props, "weight": 0.4,
                   "expression": "new_user_7d intersect high_value"})
        self.edge("crowd_derived_from", "Crowd",
                  "crowd:crm:large_driver_sticker_36", "Crowd",
                  "crowd:crm:large_driver_36_ok",
                  {**edge_props, "weight": 0.9,
                   "expression": "large_driver_sticker_36 corrected by vehicle_type=LARGE and 36 cities"})

        # 策略 -> 人群 / 标签
        self.edge("strategy_targets_crowd", "Strategy",
                  "strategy:marketing:spring_coupon", "Crowd",
                  "crowd:crm:high_value_commuters",
                  {**edge_props, "weight": 1.0, "priority": 1})
        self.edge("strategy_targets_crowd", "Strategy",
                  "strategy:fleet:sticker_incentive", "Crowd",
                  "crowd:crm:large_driver_sticker_36",
                  {**edge_props, "weight": 1.0, "priority": 1})
        self.edge("strategy_uses_tag", "Strategy",
                  "strategy:marketing:spring_coupon", "Tag",
                  "tag:crm:coupon_sensitive", {**edge_props, "weight": 0.8})

        # 画布 -> 人群 / 策略
        self.edge("canvas_references_crowd", "CanvasTask",
                  "canvas:marketing:spring_push", "Crowd",
                  "crowd:crm:high_value_commuters", edge_props)
        self.edge("canvas_references_strategy", "CanvasTask",
                  "canvas:marketing:spring_push", "Strategy",
                  "strategy:marketing:spring_coupon", edge_props)
        self.edge("canvas_references_crowd", "CanvasTask",
                  "canvas:fleet:sticker_push", "Crowd",
                  "crowd:crm:large_driver_sticker_36", edge_props)
        self.edge("canvas_references_strategy", "CanvasTask",
                  "canvas:fleet:sticker_push", "Strategy",
                  "strategy:fleet:sticker_incentive", edge_props)

        # 画布 -> 实际订单（多跳影响面终点，as-of 时间过滤示例）
        for order_id in ("order:ops:o001", "order:ops:o002", "order:ops:o003"):
            self.edge("canvas_generates_order", "CanvasTask",
                      "canvas:fleet:sticker_push", "Order", order_id,
                      edge_props)

        # 资产 -> 指标
        self.edge("asset_has_metric", "Crowd",
                  "crowd:crm:high_value_commuters", "Metric",
                  "metric:bi:gmv_lift", {**edge_props, "weight": 1.0})
        self.edge("asset_has_metric", "Crowd",
                  "crowd:crm:high_value_commuters", "Metric",
                  "metric:bi:retention_7d", {**edge_props, "weight": 0.8})
        self.edge("asset_has_metric", "Strategy",
                  "strategy:marketing:spring_coupon", "Metric",
                  "metric:bi:gmv_lift", {**edge_props, "weight": 1.0})
        self.edge("asset_has_metric", "CanvasTask",
                  "canvas:marketing:spring_push", "Metric",
                  "metric:bi:retention_7d", {**edge_props, "weight": 0.9})

        # 资产 -> 证据（覆盖多类源顶点）
        self.edge("asset_has_evidence", "Crowd",
                  "crowd:crm:high_value_commuters", "Evidence",
                  "evidence:warehouse:seg_2026_03",
                  {**edge_props, "confidence": 0.98})
        self.edge("asset_has_evidence", "Crowd",
                  "crowd:crm:new_user_7d", "Evidence",
                  "evidence:warehouse:seg_2026_03",
                  {**edge_props, "confidence": 0.9})
        self.edge("asset_has_evidence", "Strategy",
                  "strategy:marketing:spring_coupon", "Evidence",
                  "evidence:bi:abtest_042",
                  {**edge_props, "confidence": 0.96})
        self.edge("asset_has_evidence", "CanvasTask",
                  "canvas:marketing:spring_push", "Evidence",
                  "evidence:bi:abtest_042",
                  {**edge_props, "confidence": 0.96})
        self.edge("asset_has_evidence", "Tag",
                  "tag:crm:commute_rider", "Evidence",
                  "evidence:metadata:tag_audit",
                  {**edge_props, "confidence": 0.93})
        self.edge("asset_has_evidence", "Metric",
                  "metric:bi:gmv_lift", "Evidence",
                  "evidence:bi:abtest_042",
                  {**edge_props, "confidence": 0.96})
        self.edge("asset_has_evidence", "Crowd",
                  "crowd:crm:large_driver_sticker_36", "Evidence",
                  "evidence:warehouse:seg_2026_03",
                  {**edge_props, "confidence": 0.9})
        self.edge("asset_has_evidence", "Strategy",
                  "strategy:fleet:sticker_incentive", "Evidence",
                  "evidence:bi:abtest_042",
                  {**edge_props, "confidence": 0.96})
        self.edge("asset_has_evidence", "CanvasTask",
                  "canvas:fleet:sticker_push", "Evidence",
                  "evidence:bi:abtest_042",
                  {**edge_props, "confidence": 0.96})

        # 资产 -> 版本
        self.edge("asset_has_version", "Crowd", "crowd:crm:new_user_7d",
                  "Version", "version:crm:new_user_7d:v1", edge_props)
        self.edge("asset_has_version", "Crowd", "crowd:crm:new_user_7d",
                  "Version", "version:crm:new_user_7d:v2", edge_props)
        self.edge("asset_has_version", "Crowd",
                  "crowd:crm:high_value_commuters", "Version",
                  "version:crm:high_value_commuters:v1", edge_props)
        self.edge("asset_has_version", "Crowd",
                  "crowd:crm:large_driver_sticker_36", "Version",
                  "version:crm:large_driver_sticker_36:v1", edge_props)

        # 版本 -> 操作人
        self.edge("version_generated_by", "Version",
                  "version:crm:new_user_7d:v1", "Operator",
                  "operator:internal:alice",
                  {**edge_props, "action": "CREATE"})
        self.edge("version_generated_by", "Version",
                  "version:crm:new_user_7d:v2", "Operator",
                  "operator:internal:bob",
                  {**edge_props, "action": "UPDATE"})
        self.edge("version_generated_by", "Version",
                  "version:crm:high_value_commuters:v1", "Operator",
                  "operator:internal:alice",
                  {**edge_props, "action": "CREATE"})
        self.edge("version_generated_by", "Version",
                  "version:crm:large_driver_sticker_36:v1", "Operator",
                  "operator:internal:alice",
                  {**edge_props, "action": "CREATE"})

        return {
            "written": self.written,
            "violations": self.violations,
            "assets": {
                "operators": len(operators), "tags": len(tags),
                "crowds": len(crowds), "strategies": len(strategies),
                "canvases": len(canvases), "orders": len(orders),
                "cities": len(managed_cities), "metrics": len(metrics),
                "evidence": len(evidence), "versions": len(versions),
            },
        }


def seed_profile(service) -> dict:
    seeder = ProfileSeeder(service)
    return seeder.run()