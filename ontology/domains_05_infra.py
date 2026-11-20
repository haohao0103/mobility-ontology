"""域组 5：基础设施（仓储域 / 线路域 / 地理域 / 设备域 / 单证域）。"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 仓储域 ──────────────────────────────
_warehouse = [
    C("Warehouse", "仓库", "warehouse_id",
      "实体仓库。面积、月台数与作业时段决定其日处理能力，是仓配业务的基础节点。",
      "warehouse_id:id, warehouse_name:str, warehouse_type:enum, "
      "address:str, city:str, area_m2:dec, dock_count:int, "
      "operating_hours:str",
      "PLANNING,ACTIVE,SUSPENDED,CLOSED",
      aliases=("库房",), group="warehouse_domain"),
    C("WarehouseZone", "库区", "zone_id",
      "仓库内的功能分区。存储区、拣货区、暂存区、退货区的作业规则不同。",
      "zone_id:id, warehouse_id:str, zone_code:str, "
      "zone_type:enum, temperature_controlled:bool, "
      "hazard_allowed:bool",
      "ACTIVE,MAINTENANCE,CLOSED",
      aliases=("分区",), group="warehouse_domain"),
    C("WarehouseLocation", "库位", "location_id",
      "最小存储单元。库位编码决定拣货路径，编码规则混乱会显著降低效率。",
      "location_id:id, zone_id:str, location_code:str, "
      "location_type:enum, max_weight_kg:dec, "
      "max_volume_m3:dec, occupied:bool",
      "EMPTY,OCCUPIED,LOCKED,DISABLED",
      aliases=("货位",), group="warehouse_domain"),
    C("WarehouseInventory", "库存", "inventory_id",
      "库内货物的实时库存。库龄超过阈值会产生仓储费与呆滞风险。",
      "inventory_id:id, warehouse_id:str, location_id:str, "
      "sku_code:str, quantity:dec, unit:str, "
      "inbound_at:dt, age_days:int",
      "IN_STOCK,RESERVED,OUTBOUND,FROZEN",
      aliases=("存货",), group="warehouse_domain"),
    C("WarehouseInbound", "入库单", "inbound_id",
      "货物入库单据。入库必须核对数量与包装，差错在出库时才会暴露。",
      "inbound_id:id, warehouse_id:str, waybill_id:str, "
      "inbound_no:str, expected_qty:dec, actual_qty:dec, "
      "received_at:dt, operator_id:str",
      "CREATED,ARRIVED,RECEIVING,RECEIVED,EXCEPTION,CLOSED",
      aliases=("收货",), group="warehouse_domain"),
    C("WarehouseOutbound", "出库单", "outbound_id",
      "货物出库单据。出库拣货错误直接导致客户投诉。",
      "outbound_id:id, warehouse_id:str, waybill_id:str, "
      "outbound_no:str, requested_qty:dec, picked_qty:dec, "
      "shipped_at:dt, operator_id:str",
      "CREATED,PICKING,PICKED,SHIPPED,EXCEPTION,CLOSED",
      aliases=("发货",), group="warehouse_domain"),
    C("WarehouseStocktake", "盘点", "stocktake_id",
      "库存盘点记录。盘盈盘亏必须查明原因，否则会掩盖管理漏洞。",
      "stocktake_id:id, warehouse_id:str, stocktake_type:enum, "
      "planned_date:date, counted_sku:int, diff_sku:int, "
      "diff_amount:dec",
      "PLANNED,COUNTING,RECONCILING,CLOSED",
      aliases=("盘点",), group="warehouse_domain"),
    C("WarehouseTransfer", "移库", "transfer_id",
      "库内或跨库的货物移动。移库不改变所有权，但会改变库位与库龄。",
      "transfer_id:id, from_warehouse_id:str, to_warehouse_id:str, "
      "sku_code:str, quantity:dec, transferred_at:dt, "
      "transfer_reason:enum",
      "CREATED,IN_TRANSIT,RECEIVED,CLOSED",
      aliases=("转库",), group="warehouse_domain"),
    C("WarehouseHandling", "库内作业", "handling_id",
      "库内的具体作业动作。上架、拣货、复核、打包、贴标各有工时标准。",
      "handling_id:id, warehouse_id:str, handling_type:enum, "
      "sku_code:str, quantity:dec, started_at:dt, "
      "ended_at:dt, operator_id:str",
      "PENDING,DOING,DONE,REWORK",
      aliases=("库内作业",), group="warehouse_domain"),
    C("WarehouseStaff", "仓管员", "staff_id",
      "仓库作业人员。岗位与排班决定当班处理能力。",
      "staff_id:id, warehouse_id:str, staff_name:str, "
      "staff_position:enum, shift:enum, hired_at:date, "
      "skill_level:enum",
      "ACTIVE,LEAVE,RESIGNED",
      aliases=("仓管",), group="warehouse_domain"),
    C("WarehouseEquipment", "仓储设备", "equipment_id",
      "仓库内设备。叉车、堆高机、分拣线的可用性直接影响吞吐。",
      "equipment_id:id, warehouse_id:str, equipment_type:enum, "
      "equipment_no:str, capacity_kg:dec, "
      "last_maintained_at:dt, status:enum",
      "AVAILABLE,IN_USE,MAINTENANCE,FAULT",
      aliases=("库内设备",), group="warehouse_domain"),
    C("WarehouseTemperature", "温区监控", "temperature_id",
      "仓库温区的温湿度记录。超温区间与时长直接决定冷链货物是否可用。",
      "temperature_id:id, warehouse_id:str, zone_id:str, "
      "recorded_at:dt, temperature_c:dec, humidity_pct:dec, "
      "in_range:bool",
      "NORMAL,EXCURSION,FAULT", aliases=("温湿度",), group="warehouse_domain"),
    C("WarehouseCapacity", "库容", "capacity_id",
      "仓库的容量水位。库容使用率超过阈值会拒收或加收费用。",
      "capacity_id:id, warehouse_id:str, total_volume_m3:dec, "
      "used_volume_m3:dec, total_pallet:int, "
      "used_pallet:int, usage_pct:dec",
      "AVAILABLE,TIGHT,FULL",
      aliases=("库容",), group="warehouse_domain"),
    C("WarehouseFee", "仓储费", "fee_id",
      "仓储产生的费用。按体积、按托、按天三种计费方式，超期费另计。",
      "fee_id:id, warehouse_id:str, customer_id:str, "
      "fee_mode:enum, fee_amount:dec, period:str, "
      "overdue_days:int",
      "ESTIMATED,CONFIRMED,INVOICED,WAIVED",
      aliases=("仓租",), group="warehouse_domain"),
    C("WarehouseContract", "仓储合同", "contract_id",
      "与客户签订的仓储服务合同。约定费率、责任与免责条款。",
      "contract_id:id, warehouse_id:str, customer_id:str, "
      "contract_no:str, start_date:date, end_date:date, "
      "pricing_terms:json",
      "DRAFT,EFFECTIVE,EXPIRING,TERMINATED",
      aliases=("仓储合同",), group="warehouse_domain"),
    C("WarehouseSafety", "仓储安全", "safety_id",
      "仓库安全检查记录。消防通道、危品隔离、货架承重是必查项。",
      "safety_id:id, warehouse_id:str, check_items:json, "
      "check_result:enum, issues_found:int, "
      "checked_at:dt, rectified_at:dt",
      "SCHEDULED,PASSED,FAILED,RECTIFIED",
      aliases=("仓库安全",), group="warehouse_domain"),
]

# ────────────────────────────── 线路域 ──────────────────────────────
_route = [
    C("Route", "线路", "route_id",
      "常跑的运输线路。起终点、标准里程与参考时效构成线路的三要素。",
      "route_id:id, route_code:str, route_name:str, "
      "origin_code:str, dest_code:str, standard_km:dec, "
      "standard_hours:dec",
      "DRAFT,ACTIVE,SUSPENDED,RETIRED",
      aliases=("线路", "路由"), group="route_domain"),
    C("RouteSegment", "线路段", "segment_id",
      "线路拆分的路段。分段计价与分段指派都以段为单位。",
      "segment_id:id, route_id:str, segment_no:int, "
      "segment_start:str, segment_end:str, segment_km:dec, "
      "road_type:enum",
      "ACTIVE,EXPIRED", aliases=("路段",), group="route_domain"),
    C("RoutePlan", "线路规划", "plan_id",
      "一次具体的线路规划结果。多点配送的先后顺序与预计到达时间在此确定。",
      "plan_id:id, batch_id:str, plan_date:date, "
      "stop_sequence:json, total_km:dec, "
      "planner_mode:enum",
      "DRAFT,CONFIRMED,EXECUTING,DONE,CANCELLED",
      aliases=("路径规划",), group="route_domain"),
    C("RouteOptimization", "线路优化", "optimization_id",
      "线路优化算法的运行记录。优化目标（最短里程/最短时间/最低成本）需明确。",
      "optimization_id:id, plan_id:str, objective:enum, "
      "before_km:dec, after_km:dec, saved_km:dec, "
      "saved_cost:dec",
      "RUNNING,COMPLETED,FAILED", aliases=("路由优化",), group="route_domain"),
    C("RouteCost", "线路成本", "cost_id",
      "线路的标准成本。油费、路桥费、人工费、折旧费四项构成成本基线。",
      "cost_id:id, route_id:str, cost_per_trip:dec, "
      "fuel_cost:dec, toll_cost:dec, labor_cost:dec, "
      "effective_from:date",
      "CALCULATED,ACTIVE,EXPIRED",
      aliases=("线路成本",), group="route_domain"),
    C("RouteToll", "过路费", "toll_id",
      "线路的过路费标准。按车型与实际路径计算，与参考值差异大需核查。",
      "toll_id:id, route_id:str, vehicle_type:str, "
      "toll_amount:dec, toll_stations:json, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("路桥费",), group="route_domain"),
    C("RouteRestriction", "线路限制", "restriction_id",
      "线路的通行限制。限高、限重、限行时段任一命中即不可走。",
      "restriction_id:id, route_id:str, restriction_type:enum, "
      "restriction_value:str, effective_from:date, "
      "blocking:bool",
      "ACTIVE,EXPIRED", aliases=("通行限制",), group="route_domain"),
    C("RouteSchedule", "班次线路", "schedule_id",
      "固定发车的班次线路。班次时刻表是客户选择服务的关键因素。",
      "schedule_id:id, route_id:str, depart_time:str, "
      "arrive_time:str, frequency:enum, "
      "vehicle_type:str, active:bool",
      "ACTIVE,SUSPENDED,CANCELLED",
      aliases=("班次",), group="route_domain"),
    C("RouteMileage", "标准里程", "mileage_id",
      "线路的官方标准里程。用于计价与里程偏差判定。",
      "mileage_id:id, route_id:str, standard_km:dec, "
      "measure_mode:enum, measured_at:date, "
      "version:str",
      "ACTIVE,SUPERSEDED", aliases=("标准里程",), group="route_domain"),
    C("RouteTimeWindow", "时效标准", "time_window_id",
      "线路的标准运输时长。实际时长超过标准即视为延误。",
      "time_window_id:id, route_id:str, standard_hours:dec, "
      "min_hours:dec, max_hours:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("时效标准",), group="route_domain"),
    C("RouteAlternative", "备选线路", "alternative_id",
      "主线路不可用时的备选方案。备选线路的里程与时效通常更差。",
      "alternative_id:id, route_id:str, alternative_route_id:str, "
      "alternative_reason:enum, extra_km:dec, "
      "extra_hours:dec",
      "AVAILABLE,UNAVAILABLE", aliases=("备选",), group="route_domain"),
    C("RoutePerformance", "线路绩效", "performance_id",
      "线路的实际运营绩效。准时率与单公里成本是核心指标。",
      "performance_id:id, route_id:str, period:str, "
      "trip_count:int, on_time_rate:dec, "
      "cost_per_km:dec, profit_per_trip:dec",
      "CALCULATED,REVIEWED", aliases=("线路绩效",), group="route_domain"),
    C("RoutePricing", "线路报价", "pricing_id",
      "线路对外报价。线路报价是报价模板的组成部分。",
      "pricing_id:id, route_id:str, vehicle_type:str, "
      "quoted_price:dec, price_unit:enum, "
      "effective_from:date",
      "DRAFT,ACTIVE,EXPIRED", aliases=("线路价",), group="route_domain"),
    C("RouteMap", "线路地图", "map_id",
      "线路的地理形状数据。用于可视化与里程测算。",
      "map_id:id, route_id:str, polyline:json, "
      "point_count:int, generated_at:dt",
      "GENERATED,STALE", aliases=("线路图",), group="route_domain"),
    C("RouteNode", "线路节点", "node_id",
      "线路上必须经过的节点。中转场、检查站、加油站都是节点。",
      "node_id:id, route_id:str, sequence:int, "
      "node_type:enum, geo_code:str, "
      "dwell_minutes:int",
      "ACTIVE,SKIPPED", aliases=("节点",), group="route_domain"),
    C("RouteHistory", "线路历史", "history_id",
      "线路的变更历史。线路调整会影响存量订单的计价口径。",
      "history_id:id, route_id:str, change_type:enum, "
      "before_value:json, after_value:json, "
      "changed_at:dt, changed_by:str",
      "RECORDED,ARCHIVED", aliases=("线路变更",), group="route_domain"),
]

# ────────────────────────────── 地理域 ──────────────────────────────
_geo = [
    C("GeoAddress", "地址", "address_id",
      "标准化的业务地址。地址模糊是取消与延误的高频原因，需做结构化清洗。",
      "address_id:id, raw_text:str, province:str, city:str, "
      "district:str, street:str, building:str, "
      "geo_code:str, verified:bool",
      "RAW,PARSED,VERIFIED,INVALID",
      aliases=("地址",), group="geo_domain"),
    C("GeoCity", "城市", "city_id",
      "城市主数据。城市是计价规则、限行政策与运力配置的基本分区。",
      "city_id:id, city_code:str, city_name:str, "
      "province:str, level:enum, "
      "restriction_policy:str",
      "ACTIVE,INACTIVE", aliases=("城市",), group="geo_domain"),
    C("GeoDistrict", "区县", "district_id",
      "区县级行政区划。配送分区通常以区县为最小单位。",
      "district_id:id, district_code:str, district_name:str, "
      "city_code:str, delivery_zone:str",
      "ACTIVE,INACTIVE", aliases=("区县",), group="geo_domain"),
    C("GeoRegion", "区域", "region_id",
      "业务自定义的区域划分。华北大区、华东大区属管理区域，与行政区划不同。",
      "region_id:id, region_code:str, region_name:str, "
      "region_type:enum, manager_id:str, "
      "city_codes:json",
      "ACTIVE,MERGED,SPLIT", aliases=("大区",), group="geo_domain"),
    C("GeoPoint", "兴趣点", "poi_id",
      "具有业务意义的点位。工厂、园区、市场都是高频收发货点。",
      "poi_id:id, poi_name:str, poi_type:enum, "
      "geo_code:str, address:str, "
      "access_notes:txt",
      "ACTIVE,CLOSED", aliases=("POI", "点位"), group="geo_domain"),
    C("GeoFence", "电子围栏", "fence_id",
      "以坐标定义的地理围栏。到场签到与偏航告警都基于围栏判定。",
      "fence_id:id, fence_name:str, fence_type:enum, "
      "polygon:json, radius_m:dec, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("围栏",), group="geo_domain"),
    C("GeoDeliveryZone", "配送区", "zone_id",
      "配送服务的分区。跨区配送会产生跨区费。",
      "zone_id:id, zone_code:str, zone_name:str, "
      "city_code:str, boundary:json, "
      "extra_fee:dec",
      "ACTIVE,INACTIVE", aliases=("配送分区",), group="geo_domain"),
    C("GeoCoordinate", "坐标", "coordinate_id",
      "坐标点数据。不同坐标系需转换，转换误差会影响围栏判定。",
      "coordinate_id:id, latitude:dec, longitude:dec, "
      "coord_system:enum, accuracy_m:dec, "
      "captured_at:dt",
      "CAPTURED,CONVERTED", aliases=("经纬度",), group="geo_domain"),
    C("GeoDistance", "距离", "distance_id",
      "两点之间的距离。直线距离、导航距离与实际里程三者不同。",
      "distance_id:id, from_geo:str, to_geo:str, "
      "straight_km:dec, nav_km:dec, actual_km:dec, "
      "measured_time:dt",
      "MEASURED,ADJUSTED", aliases=("距离",), group="geo_domain"),
    C("GeoRestriction", "区域限行", "restriction_id",
      "区域的通行限制政策。货车限行直接影响派单可行性。",
      "restriction_id:id, city_code:str, restriction_type:enum, "
      "vehicle_types:json, time_range:str, "
      "policy_ref:str",
      "ACTIVE,EXPIRED", aliases=("限行",), group="geo_domain"),
    C("GeoGeocode", "地址解析", "geocode_id",
      "地址到坐标的解析结果。解析失败率是地址质量的直接指标。",
      "geocode_id:id, raw_address:str, resolved_geo:str, "
      "confidence:num, provider:str, "
      "resolved_at:dt",
      "SUCCESS,FAILED,AMBIGUOUS",
      aliases=("地址解析", "地理编码"), group="geo_domain"),
    C("GeoLandmark", "地标", "landmark_id",
      "具有导航意义的地标。司机找地址常靠地标而非门牌号。",
      "landmark_id:id, landmark_name:str, landmark_type:enum, "
      "geo_code:str, city_code:str",
      "ACTIVE,CLOSED", aliases=("地标",), group="geo_domain"),
    C("GeoServiceArea", "服务区域", "area_id",
      "平台或承运商的服务覆盖区域。区域外订单不予承接。",
      "area_id:id, owner_id:str, city_code:str, "
      "district_codes:json, service_level:enum",
      "ACTIVE,PAUSED,TERMINATED",
      aliases=("覆盖区",), group="geo_domain"),
    C("GeoCoverage", "覆盖范围", "coverage_id",
      "覆盖能力的量化描述。覆盖率与响应时长共同定义服务承诺。",
      "coverage_id:id, owner_id:str, period:str, "
      "covered_cities:int, coverage_rate:dec, "
      "avg_response_minutes:int",
      "CALCULATED,PUBLISHED", aliases=("覆盖",), group="geo_domain"),
]

# ────────────────────────────── 设备域 ──────────────────────────────
_device = [
    C("Device", "设备", "device_id",
      "车载或场站设备。定位、温控、摄像三类设备的组合决定可采集的数据。",
      "device_id:id, device_no:str, device_type:enum, "
      "vendor:str, model:str, imei:str, "
      "activated_at:dt",
      "REGISTERED,ACTIVE,FAULT,OFFLINE,SCRAPPED",
      aliases=("硬件",), group="device_domain"),
    C("DeviceType", "设备类型", "device_type_id",
      "设备类型的标准定义。类型决定采集能力与上报频率。",
      "device_type_id:id, type_code:str, type_name:str, "
      "capabilities:json, report_interval_s:int",
      "ACTIVE,DEPRECATED", aliases=("设备型号",), group="device_domain"),
    C("DeviceInstall", "安装记录", "install_id",
      "设备的安装记录。安装位置与接线方式影响数据质量。",
      "install_id:id, device_id:str, target_type:enum, "
      "target_id:str, installed_at:dt, "
      "installer_id:str, position:str",
      "PLANNED,INSTALLED,VERIFIED,REMOVED",
      aliases=("安装",), group="device_domain"),
    C("DeviceStatus", "设备状态", "status_id",
      "设备的实时状态。离线时长超过阈值需人工介入。",
      "status_id:id, device_id:str, online:bool, "
      "last_heartbeat_at:dt, battery_pct:dec, "
      "signal_strength:dec",
      "ONLINE,OFFLINE,FAULT",
      aliases=("设备状态",), group="device_domain"),
    C("DeviceMaintenance", "设备维护", "maintenance_id",
      "设备的维护保养记录。设备失修会导致数据断档。",
      "maintenance_id:id, device_id:str, maintenance_type:enum, "
      "performed_at:dt, performer:str, "
      "result:enum",
      "SCHEDULED,DONE,FAILED",
      aliases=("设备维护",), group="device_domain"),
    C("DeviceFault", "设备故障", "fault_id",
      "设备故障记录。故障期间数据缺失会影响里程与时长核算。",
      "fault_id:id, device_id:str, fault_code:str, "
      "occurred_at:dt, resolved_at:dt, "
      "fault_desc:txt",
      "REPORTED,DIAGNOSING,REPAIRED,REPLACED",
      aliases=("设备故障",), group="device_domain"),
    C("DeviceData", "设备数据", "data_id",
      "设备上报的原始数据。按时间序列存储，是各类分析的底层数据。",
      "data_id:id, device_id:str, collected_at:dt, "
      "data_type:enum, payload:json, "
      "quality:enum",
      "RECEIVED,PARSED,REJECTED",
      aliases=("设备数据",), group="device_domain"),
    C("DeviceFirmware", "固件", "firmware_id",
      "设备固件版本。低版本固件可能存在数据上报缺陷。",
      "firmware_id:id, firmware_device_type:str, version:str, "
      "release_date:date, features:json, "
      "mandatory:bool",
      "RELEASED,DEPRECATED,RECALLED",
      aliases=("固件",), group="device_domain"),
    C("DeviceSIM", "SIM卡", "sim_id",
      "设备使用的流量卡。流量耗尽会导致设备离线。",
      "sim_id:id, iccid:str, device_id:str, "
      "carrier:str, plan:str, "
      "expiry_date:date, balance_mb:dec",
      "ACTIVE,EXHAUSTED,SUSPENDED,EXPIRED",
      aliases=("流量卡",), group="device_domain"),
    C("DeviceAlarm", "设备告警", "alarm_id",
      "设备触发的告警。超速、偏航、断电、超温都是常见告警类型。",
      "alarm_id:id, device_id:str, alarm_type:enum, "
      "alarm_level:enum, triggered_at:dt, "
      "handled_at:dt, handled_by:str",
      "TRIGGERED,ACKNOWLEDGED,HANDLED,IGNORED",
      aliases=("告警",), group="device_domain"),
    C("DeviceConfig", "设备配置", "config_id",
      "设备参数配置。上报频率与告警阈值设置不当会产生大量噪音。",
      "config_id:id, device_id:str, config_key:str, "
      "config_value:str, applied_at:dt, "
      "applied_by:str",
      "APPLIED,ROLLED_BACK", aliases=("参数配置",), group="device_domain"),
    C("DeviceInventory", "设备台账", "inventory_id",
      "设备的在册台账。台账与实物不符是资产管理的常见问题。",
      "inventory_id:id, device_no:str, asset_no:str, "
      "location:str, book_value:dec, "
      "check_date:date",
      "IN_USE,IDLE,LOST,SCRAPPED",
      aliases=("设备台账",), group="device_domain"),
    C("DeviceScrap", "设备报废", "scrap_id",
      "设备报废记录。报废后须解绑，否则会产生幽灵数据。",
      "scrap_id:id, device_id:str, scrap_reason:enum, "
      "scrapped_at:dt, residual_value:dec, "
      "unbound:bool",
      "APPLIED,APPROVED,SCRAPPED",
      aliases=("报废",), group="device_domain"),
    C("DeviceAccessLog", "设备访问", "access_id",
      "设备数据的访问记录。设备数据涉及位置隐私，访问需留痕。",
      "access_id:id, device_id:str, accessed_by:str, "
      "accessed_at:dt, purpose:str",
      "GRANTED,DENIED", aliases=("访问",), group="device_domain"),
    C("DeviceSignal", "信号质量", "signal_id",
      "设备信号质量记录。信号差的区域会导致轨迹断点。",
      "signal_id:id, device_id:str, recorded_at:dt, "
      "signal_dbm:int, network_type:enum, "
      "geo_code:str",
      "GOOD,FAIR,POOR", aliases=("信号",), group="device_domain"),
    C("DeviceCalibration", "设备校准", "calibration_id",
      "传感器校准记录。温控与称重设备未校准会导致数据失真。",
      "calibration_id:id, device_id:str, calibration_type:enum, "
      "calibrated_at:date, result:enum, "
      "cert_no:str",
      "DUE,CALIBRATED,FAILED", aliases=("校准",), group="device_domain"),
]

# ────────────────────────────── 单证域 ──────────────────────────────
_document = [
    C("Document", "单证", "document_id",
      "业务过程中的各类单证。合同、回单、发票、许可证都以单证形式沉淀。",
      "document_id:id, document_type:enum, business_id:str, "
      "document_no:str, file_uri:str, "
      "issued_at:dt, issuer:str",
      "DRAFT,ISSUED,EFFECTIVE,VOIDED,ARCHIVED",
      aliases=("单据",), group="document_domain"),
    C("DocumentType", "单证类型", "type_id",
      "单证类型的字典定义。类型决定必填字段与审批流程。",
      "type_id:id, type_code:str, type_name:str, "
      "required_fields:json, need_approval:bool, "
      "retention_years:int",
      "ACTIVE,DEPRECATED", aliases=("单据类型",), group="document_domain"),
    C("DocumentTemplate", "单证模板", "template_id",
      "单证的版式模板。模板变更需评估存量单证的兼容性。",
      "template_id:id, template_code:str, template_name:str, "
      "type_code:str, template_uri:str, "
      "version:str",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("模板",), group="document_domain"),
    C("DocumentVersion", "单证版本", "version_id",
      "单证的历次版本。签署后修改必须产生新版本并留痕。",
      "version_id:id, document_id:str, version_no:int, "
      "file_uri:str, created_at:dt, "
      "created_by:str, change_note:txt",
      "CREATED,ACTIVE,SUPERSEDED", aliases=("版本",), group="document_domain"),
    C("DocumentSignature", "单证签署", "signature_id",
      "单证的签署记录。手签、盖章、电子签三种形式的法律效力不同。",
      "signature_id:id, document_id:str, signer_id:str, "
      "sign_mode:enum, signed_at:dt, "
      "signature_uri:str, valid:bool",
      "SIGNED,VERIFIED,INVALID,REVOKED",
      aliases=("签署",), group="document_domain"),
    C("DocumentArchive", "单证归档", "archive_id",
      "单证的归档记录。归档需满足监管留存年限并可检索。",
      "archive_id:id, document_id:str, archive_no:str, "
      "archive_uri:str, archived_at:dt, "
      "retention_years:int",
      "PENDING,ARCHIVED,RETRIEVED,DISPOSED",
      aliases=("归档",), group="document_domain"),
    C("DocumentScan", "扫描件", "scan_id",
      "纸质单证的扫描件。扫描质量差会导致 OCR 失败与人工复核成本。",
      "scan_id:id, document_id:str, scan_uri:str, "
      "page_count:int, dpi:int, "
      "quality:enum, scanned_at:dt",
      "SCANNED,QUALITY_OK,RESCAN",
      aliases=("扫描",), group="document_domain"),
    C("DocumentOCR", "OCR识别", "ocr_id",
      "单证的文字识别结果。识别准确率决定是否需要人工校对。",
      "ocr_id:id, document_id:str, engine:str, "
      "recognized_fields:json, confidence:num, "
      "recognized_at:dt",
      "PENDING,RECOGNIZED,LOW_CONFIDENCE,FAILED",
      aliases=("识别",), group="document_domain"),
    C("DocumentValidation", "单证校验", "validation_id",
      "单证的合规与逻辑校验。金额、日期、签章三项必校。",
      "validation_id:id, document_id:str, check_items:json, "
      "result:enum, failed_items:json, "
      "validated_at:dt",
      "PASSED,FAILED,WAIVED", aliases=("校验",), group="document_domain"),
    C("DocumentDelivery", "单证交付", "delivery_id",
      "单证交付给相关方的记录。回单未交付是结算挂起的常见原因。",
      "delivery_id:id, document_id:str, receiver_id:str, "
      "delivery_mode:enum, delivered_at:dt, "
      "acknowledged:bool",
      "PENDING,SENT,DELIVERED,ACKNOWLEDGED,FAILED",
      aliases=("交付",), group="document_domain"),
    C("DocumentAudit", "单证审计", "audit_id",
      "单证的变更审计。谁改了哪份单证哪个字段，必须有据可查。",
      "audit_id:id, document_id:str, action:enum, "
      "field_name:str, old_value:txt, "
      "new_value:txt, operated_at:dt",
      "RECORDED,ARCHIVED", aliases=("单证审计",), group="document_domain"),
    C("DocumentAccess", "单证访问", "access_id",
      "单证的访问记录。合同与身份类单证属敏感文件，访问需授权。",
      "access_id:id, document_id:str, accessed_by:str, "
      "accessed_at:dt, access_type:enum",
      "GRANTED,DENIED", aliases=("访问",), group="document_domain"),
    C("DocumentClassification", "单证分类", "classification_id",
      "单证按业务与密级的分类。密级决定可见范围。",
      "classification_id:id, document_id:str, biz_category:str, "
      "security_level:enum, classified_by:str, "
      "classified_at:dt",
      "CLASSIFIED,RECLASSIFIED", aliases=("分类",), group="document_domain"),
    C("DocumentRetention", "留存管理", "retention_id",
      "单证的留存期限管理。到期单证按规则销毁或转长期归档。",
      "retention_id:id, document_id:str, retention_years:int, "
      "expire_date:date, disposition:enum, "
      "disposed_at:dt",
      "RETAINED,EXPIRING,DISPOSED",
      aliases=("留存",), group="document_domain"),
    C("DocumentSeal", "用章记录", "seal_id",
      "印章使用记录。用章审批与留痕是合同风险控制的核心。",
      "seal_id:id, document_id:str, seal_type:enum, "
      "used_by:str, used_at:dt, "
      "approved_by:str",
      "APPLIED,APPROVED,USED,RETURNED",
      aliases=("用章", "盖章"), group="document_domain"),
]

_links = [
    L("warehouse_has_zone", "库区", "Warehouse", "WarehouseZone", "one_to_many"),
    L("zone_has_location", "库位", "WarehouseZone", "WarehouseLocation", "one_to_many"),
    L("warehouse_holds_inventory", "库存", "Warehouse", "WarehouseInventory", "one_to_many", temporal=True),
    L("warehouse_receives_inbound", "入库单", "Warehouse", "WarehouseInbound", "one_to_many", temporal=True),
    L("warehouse_ships_outbound", "出库单", "Warehouse", "WarehouseOutbound", "one_to_many", temporal=True),
    L("warehouse_runs_stocktake", "盘点", "Warehouse", "WarehouseStocktake", "one_to_many", temporal=True),
    L("warehouse_transfers_stock", "移库", "Warehouse", "WarehouseTransfer", "one_to_many", temporal=True),
    L("warehouse_has_handling", "库内作业", "Warehouse", "WarehouseHandling", "one_to_many", temporal=True),
    L("warehouse_employs_staff", "仓管员", "Warehouse", "WarehouseStaff", "one_to_many"),
    L("warehouse_has_equipment", "仓储设备", "Warehouse", "WarehouseEquipment", "one_to_many"),
    L("warehouse_monitors_temperature", "温区监控", "Warehouse", "WarehouseTemperature", "one_to_many", temporal=True),
    L("warehouse_has_capacity", "库容", "Warehouse", "WarehouseCapacity", "one_to_one"),
    L("warehouse_charges_fee", "仓储费", "Warehouse", "WarehouseFee", "one_to_many"),
    L("warehouse_bound_by_contract", "仓储合同", "Warehouse", "WarehouseContract", "one_to_many"),
    L("warehouse_has_safety_check", "仓储安全", "Warehouse", "WarehouseSafety", "one_to_many", temporal=True),

    L("route_has_segment", "线路段", "Route", "RouteSegment", "one_to_many"),
    L("route_planned_by", "线路规划", "Route", "RoutePlan", "one_to_many", temporal=True),
    L("route_optimized_by", "线路优化", "Route", "RouteOptimization", "one_to_many", temporal=True),
    L("route_has_cost", "线路成本", "Route", "RouteCost", "one_to_many", temporal=True),
    L("route_has_toll", "过路费", "Route", "RouteToll", "one_to_many"),
    L("route_has_restriction", "线路限制", "Route", "RouteRestriction", "one_to_many"),
    L("route_has_schedule", "班次线路", "Route", "RouteSchedule", "one_to_many"),
    L("route_has_mileage", "标准里程", "Route", "RouteMileage", "one_to_one"),
    L("route_has_time_window", "时效标准", "Route", "RouteTimeWindow", "one_to_one"),
    L("route_has_alternative", "备选线路", "Route", "RouteAlternative", "one_to_many"),
    L("route_measured_by_performance", "线路绩效", "Route", "RoutePerformance", "one_to_many", temporal=True),
    L("route_quoted_by_pricing", "线路报价", "Route", "RoutePricing", "one_to_many"),
    L("route_has_map", "线路地图", "Route", "RouteMap", "one_to_one"),
    L("route_has_node", "线路节点", "Route", "RouteNode", "one_to_many"),
    L("route_has_history", "线路历史", "Route", "RouteHistory", "one_to_many", temporal=True),

    L("address_in_city", "地址属城市", "GeoAddress", "GeoCity", "many_to_one"),
    L("city_has_district", "城市区县", "GeoCity", "GeoDistrict", "one_to_many"),
    L("region_covers_city", "区域城市", "GeoRegion", "GeoCity", "one_to_many"),
    L("address_resolves_to_poi", "地址指向点位", "GeoAddress", "GeoPoint", "many_to_one"),
    L("poi_has_fence", "电子围栏", "GeoPoint", "GeoFence", "one_to_many"),
    L("zone_delivers_to_district", "配送区", "GeoDeliveryZone", "GeoDistrict", "one_to_many"),
    L("poi_has_coordinate", "坐标", "GeoPoint", "GeoCoordinate", "one_to_one"),
    L("geo_measures_distance", "距离", "GeoPoint", "GeoDistance", "one_to_many"),
    L("city_has_restriction", "区域限行", "GeoCity", "GeoRestriction", "one_to_many"),
    L("address_geocoded_by", "地址解析", "GeoAddress", "GeoGeocode", "one_to_one"),
    L("city_has_landmark", "地标", "GeoCity", "GeoLandmark", "one_to_many"),
    L("service_area_covers_city", "服务区域", "GeoServiceArea", "GeoCity", "one_to_many"),
    L("service_area_has_coverage", "覆盖范围", "GeoServiceArea", "GeoCoverage", "one_to_one", temporal=True),

    L("device_of_type", "设备类型", "Device", "DeviceType", "many_to_one"),
    L("device_has_install", "安装记录", "Device", "DeviceInstall", "one_to_many", temporal=True),
    L("device_has_status", "设备状态", "Device", "DeviceStatus", "one_to_one", temporal=True),
    L("device_has_maintenance", "设备维护", "Device", "DeviceMaintenance", "one_to_many", temporal=True),
    L("device_has_fault", "设备故障", "Device", "DeviceFault", "one_to_many", temporal=True),
    L("device_reports_data", "设备数据", "Device", "DeviceData", "one_to_many", temporal=True),
    L("device_runs_firmware", "固件", "Device", "DeviceFirmware", "many_to_one"),
    L("device_uses_sim", "SIM卡", "Device", "DeviceSIM", "one_to_one"),
    L("device_raises_alarm", "设备告警", "Device", "DeviceAlarm", "one_to_many", temporal=True),
    L("device_has_config", "设备配置", "Device", "DeviceConfig", "one_to_many"),
    L("device_in_inventory", "设备台账", "Device", "DeviceInventory", "one_to_one"),
    L("device_scrapped_by", "设备报废", "Device", "DeviceScrap", "one_to_one"),
    L("device_accessed_via", "设备访问", "Device", "DeviceAccessLog", "one_to_many", temporal=True),
    L("device_has_signal", "信号质量", "Device", "DeviceSignal", "one_to_many", temporal=True),
    L("device_needs_calibration", "设备校准", "Device", "DeviceCalibration", "one_to_many", temporal=True),

    L("document_of_type", "单证类型", "Document", "DocumentType", "many_to_one"),
    L("document_from_template", "单证模板", "Document", "DocumentTemplate", "many_to_one"),
    L("document_has_version", "单证版本", "Document", "DocumentVersion", "one_to_many", temporal=True),
    L("document_has_signature", "单证签署", "Document", "DocumentSignature", "one_to_many"),
    L("document_archived_via", "单证归档", "Document", "DocumentArchive", "one_to_one"),
    L("document_has_scan", "扫描件", "Document", "DocumentScan", "one_to_many"),
    L("document_recognized_by_ocr", "OCR识别", "Document", "DocumentOCR", "one_to_one"),
    L("document_validated_by", "单证校验", "Document", "DocumentValidation", "one_to_one"),
    L("document_delivered_via", "单证交付", "Document", "DocumentDelivery", "one_to_many"),
    L("document_audited_by", "单证审计", "Document", "DocumentAudit", "one_to_many", temporal=True),
    L("document_accessed_via", "单证访问", "Document", "DocumentAccess", "one_to_many", temporal=True),
    L("document_classified_by", "单证分类", "Document", "DocumentClassification", "one_to_one"),
    L("document_has_retention", "留存管理", "Document", "DocumentRetention", "one_to_one"),
    L("document_uses_seal", "用章记录", "Document", "DocumentSeal", "one_to_many"),
]

DOMAINS.extend([
    {"id": "warehouse_domain", "label": "仓储域", "classes": _warehouse},
    {"id": "route_domain", "label": "线路域", "classes": _route},
    {"id": "geo_domain", "label": "地理域", "classes": _geo},
    {"id": "device_domain", "label": "设备域", "classes": _device},
    {"id": "document_domain", "label": "单证域", "classes": _document},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
