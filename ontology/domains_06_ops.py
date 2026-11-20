"""域组 6：运营侧（轨迹域 / 客服域 / 组织域 / 合同域 / 指标域）。"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 轨迹域 ──────────────────────────────
_track = [
    C("TrackPoint", "轨迹点", "point_id",
      "轨迹的单个采集点。经纬度、速度与方向构成一个点的全部信息，点是轨迹的最小单位。",
      "point_id:id, track_id:str, sequence:int, "
      "latitude:dec, longitude:dec, "
      "speed_kmh:dec, heading:dec, collected_at:dt",
      "COLLECTED,CLEANED,FLAGGED",
      aliases=("定位点",), group="track_domain"),
    C("TrackSegment", "轨迹段", "segment_id",
      "轨迹按停留或时段切分的段。分段是里程与时长计算的基础。",
      "segment_id:id, track_id:str, segment_no:int, "
      "started_at:dt, ended_at:dt, "
      "segment_km:dec, segment_type:enum",
      "COMPUTED,VERIFIED", aliases=("轨迹段",), group="track_domain"),
    C("TrackGap", "轨迹断点", "gap_id",
      "轨迹中数据缺失的区间。断点会让里程少算，必须标记而非静默跳过。",
      "gap_id:id, track_id:str, gap_start:dt, "
      "gap_end:dt, gap_minutes:int, "
      "gap_reason:enum, estimated_km:dec",
      "DETECTED,IMPUTED,CONFIRMED",
      aliases=("断点", "丢点"), group="track_domain"),
    C("TrackDeviation", "偏航记录", "deviation_id",
      "车辆偏离规划线路的记录。偏航可能意味着绕路或私自行程。",
      "deviation_id:id, track_id:str, waybill_id:str, "
      "deviated_at:dt, deviation_km:dec, "
      "deviation_minutes:int, back_at:dt",
      "DETECTED,CONFIRMED,EXPLAINED,FLAGGED",
      aliases=("偏航", "绕路"), group="track_domain"),
    C("TrackSpeed", "速度记录", "speed_id",
      "速度的统计与超速识别。超速既影响安全也影响油耗。",
      "speed_id:id, track_id:str, period_start:dt, "
      "period_end:dt, avg_speed:dec, "
      "max_speed:dec, overspeed_count:int",
      "COMPUTED,FLAGGED,CONFIRMED",
      aliases=("速度",), group="track_domain"),
    C("TrackStop", "停车记录", "stop_record_id",
      "轨迹中的停留识别。停留点与停留时长是等候费判定的原始依据。",
      "stop_record_id:id, track_id:str, stop_start:dt, "
      "stop_end:dt, stop_minutes:int, "
      "geo_code:str, track_stop_type:enum",
      "DETECTED,VERIFIED,BILLED",
      aliases=("停留", "停车"), group="track_domain"),
    C("TrackFenceEvent", "围栏事件", "event_id",
      "车辆进出电子围栏的事件。到场与离场时间由围栏事件给出，比人工签到更可信。",
      "event_id:id, track_id:str, fence_id:str, "
      "event_type:enum, event_at:dt, "
      "dwell_minutes:int",
      "ENTERED,EXITED,VERIFIED",
      aliases=("围栏事件",), group="track_domain"),
    C("TrackMileageCalc", "里程计算", "calc_id",
      "从轨迹算出的里程结果。算法版本与清洗规则会影响结果，必须记录。",
      "calc_id:id, track_id:str, raw_km:dec, "
      "cleaned_km:dec, algorithm_name:str, "
      "algorithm_version:str, calculated_at:dt",
      "COMPUTED,VERIFIED,DISPUTED",
      aliases=("里程计算",), group="track_domain"),
    C("TrackDurationCalc", "时长计算", "duration_id",
      "从轨迹算出的运输时长。含行驶时长与停留时长两部分。",
      "duration_id:id, track_id:str, total_minutes:int, "
      "driving_minutes:int, idle_minutes:int, "
      "calculated_at:dt",
      "COMPUTED,VERIFIED", aliases=("时长计算",), group="track_domain"),
    C("TrackAnomaly", "轨迹异常", "anomaly_id",
      "轨迹层面的异常识别。跳跃、漂移、长时间静止都是异常形态。",
      "anomaly_id:id, track_id:str, anomaly_type:enum, "
      "detected_at:dt, severity:enum, "
      "handled:bool",
      "DETECTED,CONFIRMED,EXONERATED,HANDLED",
      aliases=("轨迹异常",), group="track_domain"),
    C("TrackPlayback", "轨迹回放", "playback_id",
      "轨迹回放的访问记录。回放常用于争议复盘，访问本身也要留痕。",
      "playback_id:id, track_id:str, viewed_by:str, "
      "viewed_at:dt, purpose:str, "
      "clip_range:str",
      "VIEWED,EXPORTED", aliases=("回放",), group="track_domain"),
    C("TrackDataSource", "轨迹来源", "source_id",
      "轨迹数据的来源系统。自研设备、第三方平台与司机手机的可信度不同。",
      "source_id:id, track_id:str, source_system:str, "
      "report_interval_s:int, reliability:enum, "
      "connected_at:dt",
      "CONNECTED,DISCONNECTED", aliases=("轨迹来源",), group="track_domain"),
    C("TrackQuality", "轨迹质量", "quality_id",
      "轨迹数据的质量评分。点位密度、断点率与漂移率共同构成评分。",
      "quality_id:id, track_id:str, point_density:dec, "
      "gap_rate:dec, drift_rate:dec, "
      "quality_score:num, assessed_at:dt",
      "ASSESSED,ACCEPTED,REJECTED", aliases=("轨迹质量",), group="track_domain"),
    C("TrackShare", "轨迹共享", "share_id",
      "把轨迹共享给客户或承运商。共享范围与时效需精确控制。",
      "share_id:id, track_id:str, shared_with:str, "
      "share_scope:enum, expire_expire_at:dt, "
      "shared_at:dt",
      "SHARED,EXPIRED,REVOKED", aliases=("共享",), group="track_domain"),
    C("TrackPrivacyMask", "轨迹脱敏", "mask_id",
      "轨迹中敏感位置的脱敏处理。司机住址与客户仓库位置需屏蔽。",
      "mask_id:id, track_id:str, mask_type:enum, "
      "mask_range:str, applied_at:dt, "
      "applied_by:str",
      "APPLIED,REMOVED", aliases=("脱敏",), group="track_domain"),
    C("TrackArchive", "轨迹归档", "archive_id",
      "轨迹的归档与清理。热数据保留天数与冷存储策略影响查询性能。",
      "archive_id:id, track_id:str, archived_at:dt, "
      "storage_tier:enum, retention_days:int",
      "ARCHIVED,RESTORED,PURGED", aliases=("轨迹归档",), group="track_domain"),
]

# ────────────────────────────── 客服域 ──────────────────────────────
_service = [
    C("ServiceTicket", "客服工单", "service_ticket_id",
      "客户发起的需求处理单。咨询、催单、投诉都从工单开始，工单是服务闭环的载体。",
      "service_ticket_id:id, waybill_id:str, ticket_type:enum, "
      "content:txt, ticket_priority:enum, "
      "assigned_to:str, opened_at:dt, closed_at:dt",
      "CREATED,ASSIGNED,PROCESSING,PENDING_CUSTOMER,RESOLVED,CLOSED",
      aliases=("服务单",), group="service_domain"),
    C("ServiceCall", "通话记录", "call_id",
      "客服通话记录。通话时长、录音与转写是服务质量的原始依据。",
      "call_id:id, service_ticket_id:str, caller_no:str, "
      "agent_id:str, started_at:dt, duration_seconds:int, "
      "recording_uri:str",
      "RINGING,CONNECTED,ENDED,MISSED",
      aliases=("通话",), group="service_domain"),
    C("ServiceComplaint", "投诉", "complaint_id",
      "客户正式投诉。投诉等级与响应时限挂钩，超时投诉会升级。",
      "complaint_id:id, service_ticket_id:str, complaint_type:enum, "
      "complaint_level:enum, complaint_text:txt, "
      "liable_party:enum, handled_at:dt",
      "RAISED,ACCEPTED,INVESTIGATING,RESOLVED,CLOSED",
      aliases=("投诉", "客诉"), group="service_domain"),
    C("ServiceInquiry", "咨询", "inquiry_id",
      "客户咨询记录。咨询量按类型分布可反推产品与流程的薄弱环节。",
      "inquiry_id:id, service_ticket_id:str, inquiry_type:enum, "
      "question:txt, answer:txt, "
      "answered_at:dt",
      "ASKED,ANSWERED,UNRESOLVED",
      aliases=("咨询",), group="service_domain"),
    C("ServiceFeedback", "反馈", "feedback_id",
      "客户或司机的主动反馈。反馈是产品改进的直接输入。",
      "feedback_id:id, source_type:enum, source_id:str, "
      "feedback_type:enum, content:txt, "
      "submitted_at:dt, handled:bool",
      "SUBMITTED,TRIAGED,HANDLED,IGNORED",
      aliases=("意见",), group="service_domain"),
    C("ServiceRating", "评价", "rating_id",
      "服务完成后的评价。评分与标签共同构成服务质量画像。",
      "rating_id:id, waybill_id:str, rater_type:enum, "
      "score:num, tags:json, "
      "comment:txt, rated_at:dt",
      "SUBMITTED,REVIEWED,DISPUTED",
      aliases=("评分",), group="service_domain"),
    C("ServiceSatisfaction", "满意度", "satisfaction_id",
      "满意度的周期统计。NPS 与满意度是服务能力的对外指标。",
      "satisfaction_id:id, period:str, nps:num, "
      "satisfaction_score:num, sample_count:int, "
      "calculated_at:dt",
      "CALCULATED,PUBLISHED", aliases=("满意度",), group="service_domain"),
    C("ServiceKnowledge", "知识库", "knowledge_id",
      "客服使用的知识条目。知识陈旧会直接导致答错。",
      "knowledge_id:id, knowledge_code:str, title:str, "
      "content:txt, category_code:str, "
      "version:str, updated_at:dt",
      "DRAFT,PUBLISHED,DEPRECATED",
      aliases=("知识条目",), group="service_domain"),
    C("ServiceScript", "话术", "script_id",
      "特定场景的标准话术。话术违规是投诉升级的常见原因。",
      "script_id:id, script_code:str, scene:enum, "
      "content:txt, approved:bool, "
      "effective_from:date",
      "DRAFT,APPROVED,EFFECTIVE,DEPRECATED",
      aliases=("话术", "口径"), group="service_domain"),
    C("ServiceAgent", "客服坐席", "agent_id",
      "客服人员。技能组与在线状态决定工单分配。",
      "agent_id:id, agent_name:str, skill_group:enum, "
      "online_status:enum, current_load:int, "
      "join_date:date",
      "ONLINE,BUSY,OFFLINE,LEAVE",
      aliases=("坐席",), group="service_domain"),
    C("ServiceSLA", "客服时效", "sla_id",
      "客服响应的时效要求。首响时长与解决时长是两个独立指标。",
      "sla_id:id, ticket_type:enum, sla_priority:enum, "
      "first_response_minutes:int, "
      "resolve_hours:int, effective_from:date",
      "ACTIVE,EXPIRED", aliases=("客服时效",), group="service_domain"),
    C("ServiceEscalation", "服务升级", "escalation_id",
      "工单升级到更高层级。升级原因与处理结果影响责任判定。",
      "escalation_id:id, service_ticket_id:str, "
      "from_level:int, to_level:int, "
      "escalate_reason:enum, escalated_at:dt",
      "ESCALATED,ACCEPTED,RESOLVED", aliases=("升级",), group="service_domain"),
    C("ServiceResolution", "处理结果", "resolution_id",
      "工单的最终处理结论。结论必须明确责任与补偿，不能只写「已沟通」。",
      "resolution_id:id, service_ticket_id:str, "
      "resolution_type:enum, resolution_detail:txt, "
      "compensation_amount:dec, resolved_at:dt",
      "PROPOSED,ACCEPTED,REJECTED,DONE",
      aliases=("处理结论",), group="service_domain"),
    C("ServiceCallback", "回访", "callback_id",
      "服务完成后的回访记录。回访是发现隐性不满的有效手段。",
      "callback_id:id, service_ticket_id:str, "
      "callback_at:dt, result:enum, "
      "callback_note:txt",
      "SCHEDULED,DONE,UNREACHABLE,REFUSED",
      aliases=("回访",), group="service_domain"),
    C("ServiceCategory", "服务分类", "category_id",
      "服务需求的分类字典。分类决定路由到的技能组与处理流程。",
      "category_id:id, category_code:str, category_name:str, "
      "parent_code:str, skill_group:enum",
      "ACTIVE,DEPRECATED", aliases=("服务类别",), group="service_domain"),
    C("ServiceVoiceOfCustomer", "客户之声", "voc_id",
      "从多渠道汇总的客户声音分析。VOC 是产品与流程改进的决策输入。",
      "voc_id:id, period:str, channel:enum, "
      "top_topics:json, sentiment_score:num, "
      "key_quotes:json",
      "COLLECTED,ANALYZED,PUBLISHED",
      aliases=("VOC",), group="service_domain"),
]

# ────────────────────────────── 组织域 ──────────────────────────────
_organization = [
    C("Organization", "组织", "org_id",
      "企业的组织实体。组织是权限、成本与考核的归属单位。",
      "org_id:id, org_code:str, org_name:str, "
      "org_type:enum, parent_org_id:str, "
      "established_at:date",
      "ACTIVE,MERGED,DISSOLVED",
      aliases=("公司",), group="organization_domain"),
    C("Department", "部门", "department_id",
      "组织内的部门。部门决定成本中心与审批链路。",
      "department_id:id, org_id:str, dept_code:str, "
      "dept_name:str, dept_type:enum, "
      "manager_id:str, cost_center:str",
      "ACTIVE,RESTRUCTURED,DISSOLVED",
      aliases=("科室",), group="organization_domain"),
    C("Position", "岗位", "position_id",
      "岗位定义。岗位与权限绑定，人走岗留才能保证权限可控。",
      "position_id:id, position_code:str, position_name:str, "
      "position_level:enum, dept_id:str, "
      "default_roles:json",
      "ACTIVE,FROZEN,ABOLISHED",
      aliases=("职位",), group="organization_domain"),
    C("Employee", "员工", "employee_id",
      "员工主数据。工号、部门与在职状态是权限与考勤的基础。",
      "employee_id:id, employee_no:str, employee_name:str, "
      "dept_id:str, position_id:str, "
      "hired_at:date, left_at:date, employ_status:enum",
      "PROBATION,ACTIVE,LEAVE,RESIGNED,TERMINATED",
      aliases=("人员",), group="organization_domain"),
    C("EmployeeRole", "角色", "role_id",
      "权限角色的定义。角色是权限集合的命名，避免逐人授权。",
      "role_id:id, role_code:str, role_name:str, "
      "role_type:enum, permissions:json, "
      "data_scope:enum",
      "DRAFT,ACTIVE,DEPRECATED",
      aliases=("权限角色",), group="organization_domain"),
    C("Permission", "权限点", "permission_id",
      "单个权限点的定义。增删改查与数据范围是两个维度的权限。",
      "permission_id:id, permission_code:str, "
      "permission_name:str, resource:str, "
      "action:enum, sensitive:bool",
      "ACTIVE,DEPRECATED", aliases=("权限",), group="organization_domain"),
    C("UserAccount", "账号", "account_id",
      "系统登录账号。账号与员工是一对多（一人可多账号），需定期清理。",
      "account_id:id, login_name:str, employee_id:str, "
      "account_status:enum, last_login_at:dt, "
      "mfa_enabled:bool",
      "ACTIVE,LOCKED,DISABLED,DELETED",
      aliases=("登录账号",), group="organization_domain"),
    C("Team", "团队", "team_id",
      "跨部门的协作团队。团队常用于项目制与临时攻坚。",
      "team_id:id, team_code:str, team_name:str, "
      "team_type:enum, leader_id:str, "
      "member_count:int",
      "FORMING,ACTIVE,DISBANDED",
      aliases=("小组",), group="organization_domain"),
    C("Branch", "分支机构", "branch_id",
      "异地分支机构。分支机构有独立的成本与税务处理需求。",
      "branch_id:id, branch_code:str, branch_name:str, "
      "city:str, branch_type:enum, "
      "tax_registration_no:str",
      "ACTIVE,SUSPENDED,CLOSED",
      aliases=("分公司",), group="organization_domain"),
    C("BusinessUnit", "事业部", "bu_id",
      "按业务线划分的事业部。事业部的损益独立核算。",
      "bu_id:id, bu_code:str, bu_name:str, "
      "bu_type:enum, head_id:str, "
      "revenue_target:dec",
      "ACTIVE,MERGED,CLOSED",
      aliases=("业务线",), group="organization_domain"),
    C("EmployeeTraining", "员工培训", "training_id",
      "员工参加的培训记录。岗位必修课未完成不得上岗。",
      "training_id:id, employee_id:str, training_code:str, "
      "completed_date:date, score:num, "
      "passed:bool",
      "ENROLLED,ONGOING,COMPLETED,FAILED",
      aliases=("员工培训",), group="organization_domain"),
    C("EmployeePerformance", "员工绩效", "performance_id",
      "员工绩效评估结果。绩效与激励、晋升直接挂钩。",
      "performance_id:id, employee_id:str, period:str, "
      "kpi_score:num, behavior_score:num, "
      "final_grade:enum, evaluated_at:dt",
      "DRAFT,REVIEWED,CONFIRMED,APPEALED",
      aliases=("绩效考核",), group="organization_domain"),
    C("EmployeeContract", "劳动合同", "labor_contract_id",
      "劳动合同记录。合同到期未续签会产生用工风险。",
      "labor_contract_id:id, employee_id:str, "
      "contract_no:str, contract_type:enum, "
      "start_date:date, end_date:date, "
      "signed_at:dt",
      "DRAFT,SIGNED,EFFECTIVE,EXPIRING,TERMINATED",
      aliases=("劳动协议",), group="organization_domain"),
    C("OrgChart", "组织架构", "chart_id",
      "组织架构的版本快照。架构调整会连带影响权限与成本归属。",
      "chart_id:id, org_id:str, version:str, "
      "structure:json, effective_from:date, "
      "published_at:dt",
      "DRAFT,PUBLISHED,SUPERSEDED",
      aliases=("架构图",), group="organization_domain"),
    C("RoleAssignment", "角色分配", "assignment_id",
      "把角色分配给员工或账号。分配记录是权限审计的核心证据。",
      "assignment_id:id, role_id:str, assignee_type:enum, "
      "assignee_id:str, granted_at:dt, "
      "expire_expire_at:dt, granted_by:str",
      "ACTIVE,EXPIRED,REVOKED",
      aliases=("授权",), group="organization_domain"),
]

# ────────────────────────────── 合同域 ──────────────────────────────
_contract = [
    C("Contract", "合同", "contract_id",
      "商业合同主档。合同是价格、责任与结算口径的最终依据。",
      "contract_id:id, contract_no:str, contract_name:str, "
      "contract_type:enum, sign_date:date, "
      "start_date:date, end_date:date, total_amount:dec",
      "DRAFT,REVIEWING,APPROVING,SIGNED,EFFECTIVE,EXPIRING,TERMINATED",
      aliases=("协议",), group="contract_domain"),
    C("ContractClause", "合同条款", "clause_id",
      "合同的具体条款。计价、违约、免责条款是争议时的裁判依据。",
      "clause_id:id, contract_id:str, clause_no:str, "
      "clause_type:enum, clause_text:txt, "
      "binding:bool",
      "DRAFT,FINAL,BINDING",
      aliases=("条款",), group="contract_domain"),
    C("ContractParty", "合同方", "party_id",
      "合同的签署方。甲乙方权利义务、开票主体与收款主体须一致。",
      "party_id:id, contract_id:str, party_role:enum, "
      "party_name:str, unified_code:str, "
      "contact_id:str",
      "PENDING,SIGNED,WITHDRAWN",
      aliases=("签约方",), group="contract_domain"),
    C("ContractVersion", "合同版本", "version_id",
      "合同的历次版本。签署后的任何修改都必须走新版并重新签。",
      "version_id:id, contract_id:str, version_no:int, "
      "snapshot:json, created_at:dt, "
      "created_by:str",
      "CREATED,ACTIVE,SUPERSEDED", aliases=("版本",), group="contract_domain"),
    C("ContractAttachment", "合同附件", "attachment_id",
      "合同的附件材料。报价单、SLA 附件与技术协议都属附件。",
      "attachment_id:id, contract_id:str, attachment_type:enum, "
      "file_uri:str, uploaded_at:dt, verified:bool",
      "UPLOADED,VERIFIED,REJECTED", aliases=("附件",), group="contract_domain"),
    C("ContractPerformance", "合同履约", "performance_id",
      "合同履约情况的跟踪。履约率与违约次数是续签的关键依据。",
      "performance_id:id, contract_id:str, period:str, "
      "fulfilled_count:int, breach_count:int, "
      "fulfillment_rate:dec",
      "TRACKING,ASSESSED,CONFIRMED",
      aliases=("履约",), group="contract_domain"),
    C("ContractBreach", "合同违约", "breach_id",
      "违约事实与责任。违约认定需引用具体条款，否则无法主张。",
      "breach_id:id, contract_id:str, breach_type:enum, "
      "breach_detail:txt, liable_party:enum, "
      "penalty_amount:dec, confirmed_at:dt",
      "ALLEGED,INVESTIGATING,CONFIRMED,DISPUTED,WAIVED",
      aliases=("违约",), group="contract_domain"),
    C("ContractRenewal", "合同续签", "renewal_id",
      "合同续签流程。续签前必须评估上一周期的履约与价格。",
      "renewal_id:id, contract_id:str, new_contract_id:str, "
      "renewal_mode:enum, price_change_pct:dec, "
      "renewed_at:dt",
      "INITIATED,NEGOTIATING,SIGNED,LAPSED",
      aliases=("续签",), group="contract_domain"),
    C("ContractTermination", "合同终止", "termination_id",
      "合同提前终止记录。终止的清算条款决定成本规模。",
      "termination_id:id, contract_id:str, "
      "termination_type:enum, termination_reason:txt, "
      "settlement_amount:dec, terminated_at:dt",
      "REQUESTED,APPROVED,EFFECTIVE,SETTLED",
      aliases=("解约",), group="contract_domain"),
    C("ContractTemplate", "合同模板", "template_id",
      "标准合同模板。模板的条款库决定了合同的基线风险水平。",
      "template_id:id, template_code:str, template_name:str, "
      "contract_type:enum, clauses:json, version:str",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("模板",), group="contract_domain"),
    C("ContractReview", "合同评审", "review_id",
      "合同签署前的法务与财务评审。评审意见必须闭环。",
      "review_id:id, contract_id:str, review_type:enum, "
      "reviewer_id:str, review_opinion:txt, "
      "reviewed_at:dt, passed:bool",
      "PENDING,IN_REVIEW,PASSED,REJECTED",
      aliases=("评审",), group="contract_domain"),
    C("ContractPrice", "合同价格", "price_id",
      "合同约定的价格条款。价格有效期与调价机制必须明确。",
      "price_id:id, contract_id:str, price_item:str, "
      "price_value:dec, price_unit:enum, "
      "effective_from:date, adjustable:bool",
      "DRAFT,EFFECTIVE,SUPERSEDED,EXPIRED",
      aliases=("合同价",), group="contract_domain"),
    C("ContractDeposit", "保证金", "deposit_id",
      "合同约定的保证金。保证金退还需满足合同终止条件。",
      "deposit_id:id, contract_id:str, deposit_amount:dec, "
      "paid_at:dt, refundable_at:date, "
      "refunded_amount:dec",
      "PENDING,PAID,PARTIAL,REFUNDED,FORFEITED",
      aliases=("押金",), group="contract_domain"),
    C("ContractAmendment", "合同变更", "amendment_id",
      "合同变更记录。变更须双方确认，单方修改无效。",
      "amendment_id:id, contract_id:str, change_type:enum, "
      "before_value:json, after_value:json, "
      "effective_date:date, confirmed_by:str",
      "PROPOSED,CONFIRMED,EFFECTIVE,REJECTED",
      aliases=("变更",), group="contract_domain"),
    C("ContractObligation", "合同义务", "obligation_id",
      "合同规定的具体义务项。义务履行有期限，逾期即违约。",
      "obligation_id:id, contract_id:str, obligor:enum, "
      "obligation_desc:txt, due_date:date, "
      "fulfilled:bool",
      "PENDING,FULFILLED,OVERDUE,WAIVED",
      aliases=("义务",), group="contract_domain"),
]

# ────────────────────────────── 指标域 ──────────────────────────────
_metric = [
    C("Metric", "指标", "metric_id",
      "业务指标主档。指标是管理的语言，定义不清的指标会引发大量争论。",
      "metric_id:id, metric_code:str, metric_name:str, "
      "metric_type:enum, unit:str, "
      "direction:enum",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("度量",), group="metric_domain"),
    C("MetricDefinition", "指标定义", "definition_id",
      "指标的计算口径。分子分母、过滤条件与时间口径必须写死。",
      "definition_id:id, metric_id:str, formula_text:txt, "
      "numerator:str, denominator:str, "
      "filters:json, version:str",
      "DRAFT,ACTIVE,SUPERSEDED", aliases=("口径",), group="metric_domain"),
    C("MetricValue", "指标值", "value_id",
      "指标在某个周期与维度的实际值。值必须带周期与维度，否则无意义。",
      "value_id:id, metric_id:str, period:str, "
      "dimension_key:str, metric_value:dec, "
      "computed_at:dt",
      "COMPUTED,VERIFIED,PUBLISHED", aliases=("指标值",), group="metric_domain"),
    C("MetricTarget", "指标目标", "target_id",
      "指标的考核目标值。目标与实际的差距是经营分析的核心。",
      "target_id:id, metric_id:str, period:str, "
      "target_value:dec, owner_id:str, "
      "set_at:dt",
      "DRAFT,SET,ADJUSTED", aliases=("目标",), group="metric_domain"),
    C("MetricDimension", "指标维度", "dimension_id",
      "指标的切片维度。城市、车型、客户是最常用的三个维度。",
      "dimension_id:id, metric_id:str, dimension_code:str, "
      "dimension_name:str, values:json",
      "ACTIVE,DEPRECATED", aliases=("维度",), group="metric_domain"),
    C("MetricSnapshot", "指标快照", "snapshot_id",
      "指标在特定时点的冻结快照。快照用于复盘时避免数据被重算覆盖。",
      "snapshot_id:id, metric_id:str, snapshot_at:dt, "
      "snapshot_value:dec, calc_version:str",
      "CAPTURED,VERIFIED", aliases=("快照",), group="metric_domain"),
    C("MetricAlert", "指标告警", "alert_id",
      "指标异常触发的告警。阈值与持续时长共同决定是否告警。",
      "alert_id:id, metric_id:str, threshold:dec, "
      "actual_value:dec, alert_level:enum, "
      "triggered_at:dt, handled_at:dt",
      "TRIGGERED,ACKNOWLEDGED,HANDLED,IGNORED",
      aliases=("指标告警",), group="metric_domain"),
    C("KPI", "考核指标", "kpi_id",
      "纳入考核的关键指标。KPI 与激励挂钩，口径变更须慎重。",
      "kpi_id:id, metric_id:str, kpi_name:str, "
      "weight:dec, target_value:dec, "
      "assessment_period:enum",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("KPI",), group="metric_domain"),
    C("Dashboard", "看板", "dashboard_id",
      "指标看板。看板布局反映管理者关注重点。",
      "dashboard_id:id, dashboard_name:str, "
      "owner_id:str, metric_ids:json, "
      "layout:json, published:bool",
      "DRAFT,PUBLISHED,ARCHIVED",
      aliases=("报表页",), group="metric_domain"),
    C("Report", "报表", "report_id",
      "定期生成的业务报表。报表数据须可下钻到明细。",
      "report_id:id, report_code:str, report_name:str, "
      "report_type:enum, period:str, "
      "generated_at:dt, file_uri:str",
      "DRAFT,GENERATED,PUBLISHED,ARCHIVED",
      aliases=("报表",), group="metric_domain"),
    C("ReportSubscription", "报表订阅", "subscription_id",
      "报表的订阅与推送配置。订阅错人会泄露敏感经营数据。",
      "subscription_id:id, report_id:str, subscriber_id:str, "
      "channel:enum, frequency:enum, "
      "active:bool",
      "ACTIVE,PAUSED,CANCELLED",
      aliases=("订阅",), group="metric_domain"),
    C("DataLineage", "数据血缘", "lineage_id",
      "指标到源表的血缘链路。血缘不清就无法定位数据问题。",
      "lineage_id:id, target_id:str, source_type:enum, "
      "source_id:str, transform:str, "
      "recorded_at:dt",
      "RECORDED,VERIFIED,STALE",
      aliases=("血缘",), group="metric_domain"),
    C("StatisticPeriod", "统计周期", "period_id",
      "统计的时间粒度。日、周、月、季四种粒度不可混用。",
      "period_id:id, period_code:str, period_type:enum, "
      "start_date:date, end_date:date",
      "OPEN,CLOSED,LOCKED", aliases=("周期",), group="metric_domain"),
    C("MetricAttribution", "指标归因", "attribution_id",
      "指标变化的归因分析。归因到可行动的因素才有管理价值。",
      "attribution_id:id, metric_id:str, period:str, "
      "change_value:dec, main_factors:json, "
      "explained_pct:dec, analyzed_at:dt",
      "ANALYZING,COMPLETED,REVIEWED",
      aliases=("归因",), group="metric_domain"),
]

_links = [
    L("track_has_point", "轨迹点", "Track", "TrackPoint", "one_to_many", temporal=True),
    L("track_has_segment", "轨迹段", "Track", "TrackSegment", "one_to_many"),
    L("track_has_gap", "轨迹断点", "Track", "TrackGap", "one_to_many"),
    L("track_has_deviation", "偏航记录", "Track", "TrackDeviation", "one_to_many", temporal=True),
    L("track_measures_speed", "速度记录", "Track", "TrackSpeed", "one_to_many"),
    L("track_has_stop_record", "停车记录", "Track", "TrackStop", "one_to_many", temporal=True),
    L("track_has_fence_event", "围栏事件", "Track", "TrackFenceEvent", "one_to_many", temporal=True),
    L("track_has_mileage_calc", "里程计算", "Track", "TrackMileageCalc", "one_to_one"),
    L("track_has_duration_calc", "时长计算", "Track", "TrackDurationCalc", "one_to_one"),
    L("track_has_anomaly", "轨迹异常", "Track", "TrackAnomaly", "one_to_many"),
    L("track_played_back", "轨迹回放", "Track", "TrackPlayback", "one_to_many", temporal=True),
    L("track_from_source", "轨迹来源", "Track", "TrackDataSource", "many_to_one"),
    L("track_measured_quality", "轨迹质量", "Track", "TrackQuality", "one_to_one"),
    L("track_shared_via", "轨迹共享", "Track", "TrackShare", "one_to_many"),
    L("track_masked_by", "轨迹脱敏", "Track", "TrackPrivacyMask", "one_to_one"),
    L("track_archived_via", "轨迹归档", "Track", "TrackArchive", "one_to_one"),

    L("service_ticket_has_call", "通话记录", "ServiceTicket", "ServiceCall", "one_to_many"),
    L("service_ticket_becomes_complaint", "投诉", "ServiceTicket", "ServiceComplaint", "one_to_one"),
    L("service_ticket_is_inquiry", "咨询", "ServiceTicket", "ServiceInquiry", "one_to_one"),
    L("feedback_from_source", "反馈", "ServiceTicket", "ServiceFeedback", "many_to_many"),
    L("waybill_rated_by", "评价", "Waybill", "ServiceRating", "one_to_many"),
    L("service_measures_satisfaction", "满意度", "ServiceTicket", "ServiceSatisfaction", "many_to_many"),
    L("agent_uses_knowledge", "知识库", "ServiceAgent", "ServiceKnowledge", "many_to_many"),
    L("agent_uses_script", "话术", "ServiceAgent", "ServiceScript", "many_to_many"),
    L("ticket_assigned_to_agent", "工单分配", "ServiceTicket", "ServiceAgent", "many_to_one"),
    L("ticket_bound_by_sla", "客服时效", "ServiceTicket", "ServiceSLA", "many_to_one"),
    L("ticket_escalated_by", "服务升级", "ServiceTicket", "ServiceEscalation", "one_to_many", temporal=True),
    L("ticket_resolved_by", "处理结果", "ServiceTicket", "ServiceResolution", "one_to_one"),
    L("ticket_followed_by_callback", "回访", "ServiceTicket", "ServiceCallback", "one_to_many"),
    L("ticket_classified_as", "服务分类", "ServiceTicket", "ServiceCategory", "many_to_one"),
    L("service_collects_voc", "客户之声", "ServiceTicket", "ServiceVoiceOfCustomer", "many_to_many"),

    L("org_has_department", "部门", "Organization", "Department", "one_to_many"),
    L("department_has_position", "岗位", "Department", "Position", "one_to_many"),
    L("employee_holds_position", "员工岗位", "Employee", "Position", "many_to_one"),
    L("employee_has_role", "员工角色", "Employee", "EmployeeRole", "many_to_many"),
    L("role_grants_permission", "角色权限", "EmployeeRole", "Permission", "many_to_many"),
    L("employee_owns_account", "账号", "Employee", "UserAccount", "one_to_many"),
    L("employee_in_team", "团队", "Employee", "Team", "many_to_many"),
    L("org_has_branch", "分支机构", "Organization", "Branch", "one_to_many"),
    L("org_has_business_unit", "事业部", "Organization", "BusinessUnit", "one_to_many"),
    L("employee_takes_training", "员工培训", "Employee", "EmployeeTraining", "one_to_many", temporal=True),
    L("employee_measured_by_performance", "员工绩效", "Employee", "EmployeePerformance", "one_to_many", temporal=True),
    L("employee_bound_by_labor_contract", "劳动合同", "Employee", "EmployeeContract", "one_to_one"),
    L("org_has_chart", "组织架构", "Organization", "OrgChart", "one_to_many", temporal=True),
    L("role_assigned_via", "角色分配", "EmployeeRole", "RoleAssignment", "one_to_many", temporal=True),

    L("contract_has_clause", "合同条款", "Contract", "ContractClause", "one_to_many"),
    L("contract_has_party", "合同方", "Contract", "ContractParty", "one_to_many"),
    L("contract_has_version", "合同版本", "Contract", "ContractVersion", "one_to_many", temporal=True),
    L("contract_has_attachment", "合同附件", "Contract", "ContractAttachment", "one_to_many"),
    L("contract_measured_by_performance", "合同履约", "Contract", "ContractPerformance", "one_to_many", temporal=True),
    L("contract_has_breach", "合同违约", "Contract", "ContractBreach", "one_to_many"),
    L("contract_renewed_by", "合同续签", "Contract", "ContractRenewal", "one_to_one"),
    L("contract_terminated_by", "合同终止", "Contract", "ContractTermination", "one_to_one"),
    L("contract_from_template", "合同模板", "Contract", "ContractTemplate", "many_to_one"),
    L("contract_reviewed_by", "合同评审", "Contract", "ContractReview", "one_to_many"),
    L("contract_has_price", "合同价格", "Contract", "ContractPrice", "one_to_many", temporal=True),
    L("contract_has_deposit", "保证金", "Contract", "ContractDeposit", "one_to_one"),
    L("contract_amended_by", "合同变更", "Contract", "ContractAmendment", "one_to_many", temporal=True),
    L("contract_has_obligation", "合同义务", "Contract", "ContractObligation", "one_to_many"),

    L("metric_has_definition", "指标定义", "Metric", "MetricDefinition", "one_to_many", temporal=True),
    L("metric_has_value", "指标值", "Metric", "MetricValue", "one_to_many", temporal=True),
    L("metric_has_target", "指标目标", "Metric", "MetricTarget", "one_to_many", temporal=True),
    L("metric_has_dimension", "指标维度", "Metric", "MetricDimension", "one_to_many"),
    L("metric_has_snapshot", "指标快照", "Metric", "MetricSnapshot", "one_to_many", temporal=True),
    L("metric_raises_alert", "指标告警", "Metric", "MetricAlert", "one_to_many", temporal=True),
    L("kpi_based_on_metric", "考核指标", "KPI", "Metric", "many_to_one"),
    L("dashboard_shows_metric", "看板", "Dashboard", "Metric", "many_to_many"),
    L("report_based_on_metric", "报表", "Report", "Metric", "many_to_many"),
    L("report_has_subscription", "报表订阅", "Report", "ReportSubscription", "one_to_many"),
    L("metric_has_lineage", "数据血缘", "Metric", "DataLineage", "one_to_many"),
    L("metric_uses_period", "统计周期", "Metric", "StatisticPeriod", "many_to_one"),
    L("metric_has_attribution", "指标归因", "Metric", "MetricAttribution", "one_to_many", temporal=True),
]

DOMAINS.extend([
    {"id": "track_domain", "label": "轨迹域", "classes": _track},
    {"id": "service_domain", "label": "客服域", "classes": _service},
    {"id": "organization_domain", "label": "组织域", "classes": _organization},
    {"id": "contract_domain", "label": "合同域", "classes": _contract},
    {"id": "metric_domain", "label": "指标域", "classes": _metric},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
