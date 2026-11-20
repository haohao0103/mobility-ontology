"""域组 4：风险与合规（证据域 / 异常域 / 保险域 / 关务域 / 合规域）。"""
from __future__ import annotations

from . import C, L

DOMAINS: list[dict] = []

# ────────────────────────────── 证据域 ──────────────────────────────
_evidence = [
    C("EvidencePackage", "证据包", "package_id",
      "围绕一个争议事项打包的证据集合。证据包是提交给客户或保险方的完整举证单元。",
      "package_id:id, subject_type:enum, subject_id:str, "
      "package_no:str, item_count:int, sealed:bool, "
      "sealed_at:dt",
      "BUILDING,SEALED,SUBMITTED,ACCEPTED,REJECTED",
      aliases=("举证包",), group="evidence_domain"),
    C("EvidenceChain", "证据链", "chain_id",
      "多个证据之间的时序与因果关系。单点证据不足，成链才能支撑结论。",
      "chain_id:id, subject_id:str, chain_type:enum, "
      "evidence_ids:json, chain_gap:json, "
      "verified:bool",
      "DRAFT,VERIFIED,GAP_FOUND,REJECTED",
      aliases=("证据链",), group="evidence_domain"),
    C("EvidencePhoto", "照片证据", "photo_id",
      "照片类证据。拍摄时点、位置与内容哈希共同保证其可信度。",
      "photo_id:id, evidence_id:str, photo_uri:str, "
      "taken_at:dt, geo_code:str, content_hash:str, "
      "scene:enum",
      "CAPTURED,VERIFIED,REJECTED", aliases=("照片",), group="evidence_domain"),
    C("EvidenceVideo", "视频证据", "video_id",
      "视频类证据。时长与关键帧截图需一并留存，长视频要标注关键时点。",
      "video_id:id, evidence_id:str, video_uri:str, "
      "duration_seconds:int, key_frame_at:json, "
      "content_hash:str",
      "CAPTURED,VERIFIED,REJECTED", aliases=("视频",), group="evidence_domain"),
    C("EvidenceAudio", "录音证据", "audio_id",
      "录音类证据。客服与司机的通话录音是费用争议的常见凭证。",
      "audio_id:id, evidence_id:str, audio_uri:str, "
      "duration_seconds:int, transcript:txt, "
      "content_hash:str",
      "CAPTURED,TRANSCRIBED,VERIFIED,REJECTED",
      aliases=("录音",), group="evidence_domain"),
    C("EvidenceDocument", "文档证据", "document_id",
      "文档类证据。合同、清单、报告都属此类，需保留原始文件与扫描件。",
      "document_id:id, evidence_id:str, document_type:enum, "
      "file_uri:str, page_count:int, content_hash:str",
      "UPLOADED,VERIFIED,REJECTED", aliases=("文档",), group="evidence_domain"),
    C("EvidenceSignature", "电子签名", "signature_id",
      "电子签名证据。签署人与签署时点经证书校验后具备法律效力。",
      "signature_id:id, evidence_id:str, signer_id:str, "
      "signed_at:dt, cert_serial:str, "
      "signature_value:str",
      "SIGNED,VERIFIED,INVALID", aliases=("电子签",), group="evidence_domain"),
    C("EvidenceTimestamp", "可信时间戳", "timestamp_id",
      "第三方可信时间戳。证明证据在该时点已存在且未被篡改。",
      "timestamp_id:id, evidence_id:str, tsa_name:str, "
      "timestamp_value:str, created_at:dt",
      "ISSUED,VERIFIED,EXPIRED", aliases=("时间戳",), group="evidence_domain"),
    C("EvidenceHash", "哈希存证", "hash_id",
      "证据内容的哈希指纹。上链或存证平台可证明内容未变。",
      "hash_id:id, evidence_id:str, algorithm:enum, "
      "hash_value:str, stored_at:dt, chain_tx_id:str",
      "COMPUTED,ANCHORED,VERIFIED", aliases=("哈希", "存证"), group="evidence_domain"),
    C("EvidenceSource", "证据来源", "source_id",
      "证据的产生系统与采集方式。系统自动采集的可信度高于人工上传。",
      "source_id:id, evidence_id:str, source_system:str, "
      "collection_mode:enum, collector_id:str, "
      "collected_at:dt",
      "RECORDED,VERIFIED", aliases=("来源",), group="evidence_domain"),
    C("EvidenceValidity", "有效性判定", "validity_id",
      "证据的有效性裁定。关联性、合法性、真实性三项全过才算有效。",
      "validity_id:id, evidence_id:str, relevance:bool, "
      "legality:bool, authenticity:bool, "
      "conclusion:enum, judged_by:str",
      "PENDING,JUDGED,APPEALED", aliases=("有效性",), group="evidence_domain"),
    C("EvidenceTamperCheck", "防篡改校验", "tamper_check_id",
      "证据完整性校验记录。哈希比对不一致即视为被篡改。",
      "tamper_check_id:id, evidence_id:str, expected_hash:str, "
      "actual_hash:str, check_result:enum, checked_at:dt",
      "PASSED,FAILED,ERROR", aliases=("校验",), group="evidence_domain"),
    C("EvidenceCollectionTask", "取证任务", "task_id",
      "派给一线人员的取证任务。任务清单明确要求拍什么、拍几张、什么时候拍。",
      "task_id:id, subject_id:str, task_type:enum, "
      "required_items:json, assignee_id:str, due_at:dt, "
      "completed_at:dt",
      "ASSIGNED,IN_PROGRESS,SUBMITTED,RETURNED,COMPLETED",
      aliases=("取证",), group="evidence_domain"),
    C("EvidenceRetention", "证据留存", "retention_id",
      "证据的留存期限与到期处理。诉讼时效内的证据不得删除。",
      "retention_id:id, evidence_id:str, retention_years:int, "
      "expire_date:date, disposition:enum, "
      "disposed_at:dt",
      "RETAINED,EXPIRING,DISPOSED", aliases=("留存",), group="evidence_domain"),
    C("EvidenceAccess", "证据访问", "access_id",
      "证据的访问记录。谁在何时查看了哪份证据，访问本身也要留痕。",
      "access_id:id, evidence_id:str, accessed_by:str, "
      "accessed_at:dt, access_reason:str, ip_address:str",
      "GRANTED,DENIED", aliases=("访问",), group="evidence_domain"),
]

# ────────────────────────────── 异常域 ──────────────────────────────
_exception = [
    C("ExceptionCategory", "异常分类", "category_id",
      "异常的分类字典。货损、延误、爽约、拒收四大类下再分细项，分类决定处理流程。",
      "category_id:id, category_code:str, category_name:str, "
      "parent_code:str, default_severity:enum, "
      "responsible_role:str",
      "ACTIVE,DEPRECATED", aliases=("异常类型",), group="exception_domain"),
    C("ExceptionReport", "异常上报", "report_id",
      "一线人员上报的异常。上报必须带现场证据，否则无法进入调查。",
      "report_id:id, waybill_id:str, category_code:str, "
      "report_desc:txt, reported_by:str, reported_at:dt, "
      "geo_code:str, evidence_ids:json",
      "SUBMITTED,TRIAGED,ACCEPTED,REJECTED",
      aliases=("上报", "报案"), group="exception_domain"),
    C("ExceptionInvestigation", "异常调查", "investigation_id",
      "异常事实调查记录。调查要回答发生了什么、何时何地、谁知道。",
      "investigation_id:id, exception_id:str, investigator_id:str, "
      "findings:json, concluded_at:dt, "
      "conclusion_text:txt",
      "OPEN,IN_PROGRESS,CONCLUDED,REOPENED",
      aliases=("调查",), group="exception_domain"),
    C("ExceptionAttribution", "责任归因", "attribution_id",
      "异常责任方的判定。归因到客户、司机、承运商、场站或平台，决定费用承担。",
      "attribution_id:id, exception_id:str, liable_party:enum, "
      "attribution_basis:str, attribution_pct:dec, "
      "attributed_by:str, disputed:bool",
      "PENDING,ATTRIBUTED,DISPUTED,CONFIRMED",
      aliases=("定责",), group="exception_domain"),
    C("ExceptionResolution", "异常处理", "resolution_id",
      "异常的处理方案与执行结果。补货、换车、退款、赔付都在此记录。",
      "resolution_id:id, exception_id:str, resolution_type:enum, "
      "resolution_detail:txt, cost_incurred:dec, "
      "resolved_at:dt",
      "PLANNED,EXECUTING,DONE,FAILED",
      aliases=("处理",), group="exception_domain"),
    C("ExceptionEscalation", "异常升级", "escalation_id",
      "异常升级到更高层级的记录。超时未处理或金额超限自动升级。",
      "escalation_id:id, exception_id:str, from_level:int, "
      "to_level:int, escalate_reason:enum, "
      "escalated_at:dt, handler_id:str",
      "ESCALATED,ACCEPTED,RESOLVED", aliases=("升级",), group="exception_domain"),
    C("ExceptionClosure", "异常关闭", "closure_id",
      "异常关闭的结论。关闭前必须处理完成且责任费用已落实。",
      "closure_id:id, exception_id:str, closure_type:enum, "
      "closure_note:txt, closed_at:dt, closed_by:str",
      "CHECKING,CLOSED,REOPENED", aliases=("关闭",), group="exception_domain"),
    C("ExceptionPenalty", "异常处罚", "penalty_id",
      "因异常对各方的处罚。处罚金额与依据必须可追溯。",
      "penalty_id:id, exception_id:str, penalized_party:enum, "
      "penalty_amount:dec, penalty_basis:str, "
      "executed_at:dt, disputed:bool",
      "PROPOSED,EXECUTED,DISPUTED,REVOKED",
      aliases=("处罚",), group="exception_domain"),
    C("ExceptionCompensation", "异常赔付", "compensation_id",
      "向受害方的赔付。赔付上限受货值申报与保险条款约束。",
      "compensation_id:id, exception_id:str, compensation_amount:dec, "
      "compensation_basis:enum, paid_at:dt, "
      "payer:enum",
      "CALCULATED,APPROVED,PAID,REJECTED",
      aliases=("赔付",), group="exception_domain"),
    C("ExceptionClaim", "理赔申请", "claim_id",
      "向保险公司发起的理赔。理赔材料完整性决定到账速度。",
      "claim_id:id, exception_id:str, policy_id:str, "
      "claim_amount:dec, materials:json, "
      "submitted_at:dt, settled_at:dt",
      "DRAFT,SUBMITTED,REVIEWING,APPROVED,REJECTED,PAID",
      aliases=("理赔",), group="exception_domain"),
    C("ExceptionRootCause", "根因分析", "root_cause_id",
      "异常的根因定位。只处理表象不查根因，同类异常会反复发生。",
      "root_cause_id:id, exception_id:str, root_cause:enum, "
      "analysis_method:enum, five_why:json, "
      "identified_at:dt",
      "ANALYZING,IDENTIFIED,VERIFIED",
      aliases=("根因",), group="exception_domain"),
    C("ExceptionPrevention", "预防措施", "prevention_id",
      "针对根因制定的预防措施。措施有效性需在后续周期内验证。",
      "prevention_id:id, root_cause_id:str, measure:txt, "
      "owner_id:str, due_date:date, "
      "effective_checked:bool",
      "PLANNED,IN_PROGRESS,DONE,INEFFECTIVE",
      aliases=("预防",), group="exception_domain"),
    C("ExceptionSLA", "异常时效", "exception_sla_id",
      "异常处理的时效要求。不同等级异常的响应与闭环时限不同。",
      "exception_sla_id:id, exception_level:enum, "
      "response_minutes:int, resolve_hours:int, "
      "breach_penalty:dec",
      "ACTIVE,EXPIRED", aliases=("异常时效",), group="exception_domain"),
    C("ExceptionNotification", "异常通知", "notification_id",
      "异常的通知记录。客户、司机、调度各需不同话术的通知。",
      "notification_id:id, exception_id:str, notify_target:enum, "
      "channel:enum, sent_at:dt, delivered:bool",
      "PENDING,SENT,DELIVERED,FAILED",
      aliases=("通知",), group="exception_domain"),
    C("ExceptionReview", "异常复盘", "review_id",
      "定期或专项的异常复盘会记录。复盘要出结论与动作，不能只开会。",
      "review_id:id, period:str, exception_count:int, "
      "key_findings:json, action_items:json, "
      "reviewed_at:dt",
      "SCHEDULED,DONE,FOLLOW_UP",
      aliases=("复盘",), group="exception_domain"),
    C("ExceptionTrend", "异常趋势", "trend_id",
      "异常按维度的趋势统计。趋势上升的维度是治理重点。",
      "trend_id:id, period:str, dimension:enum, "
      "dimension_value:str, count:int, "
      "change_pct:dec",
      "COMPUTED,PUBLISHED", aliases=("趋势",), group="exception_domain"),
]

# ────────────────────────────── 保险域 ──────────────────────────────
_insurance = [
    C("InsurancePolicy", "保险单", "policy_id",
      "货物运输险或责任险的保单。保额、免赔与责任范围决定可赔付规模。",
      "policy_id:id, policy_no:str, insurer:str, "
      "coverage_type:enum, coverage_amount:dec, "
      "deductible:dec, start_date:date, end_date:date",
      "QUOTED,EFFECTIVE,EXPIRING,EXPIRED,TERMINATED",
      aliases=("保单",), group="insurance_domain"),
    C("InsuranceApplication", "投保申请", "application_id",
      "投保申请单。货物性质与运输路线决定费率与是否承保。",
      "application_id:id, policy_type:enum, applicant_id:str, "
      "insured_subject:str, insured_amount:dec, "
      "applied_at:dt, approved_at:dt",
      "DRAFT,SUBMITTED,UNDERWRITING,APPROVED,REJECTED",
      aliases=("投保",), group="insurance_domain"),
    C("InsurancePremium", "保费", "premium_id",
      "保单的保费金额与计算方式。按单、按月、按货值三种计价方式。",
      "premium_id:id, policy_id:str, premium_amount:dec, "
      "premium_mode:enum, paid_at:dt, "
      "period:str",
      "CALCULATED,PAID,REFUNDED",
      aliases=("保费",), group="insurance_domain"),
    C("InsuranceCoverage", "保险责任", "coverage_id",
      "保单承担的具体责任项。碰撞、盗抢、雨淋、货损各有是否承保与限额。",
      "coverage_id:id, policy_id:str, risk_code:enum, "
      "covered:bool, coverage_limit:dec, "
      "exclusions:txt",
      "ACTIVE,EXPIRED", aliases=("责任范围",), group="insurance_domain"),
    C("InsuranceClaim", "理赔案件", "claim_id",
      "保险理赔案件。从报案到结案，每个环节都有时限要求。",
      "claim_id:id, policy_id:str, waybill_id:str, "
      "claim_amount:dec, incident_at:dt, "
      "reported_at:dt, settled_at:dt",
      "REPORTED,DOCUMENTING,SURVEYING,ASSESSED,APPROVED,REJECTED,PAID",
      aliases=("理赔案",), group="insurance_domain"),
    C("InsuranceClaimDocument", "理赔材料", "document_id",
      "理赔所需的材料清单。缺一份材料就会拖一周。",
      "document_id:id, claim_id:str, document_type:enum, "
      "file_uri:str, required:bool, "
      "submitted_at:dt",
      "MISSING,SUBMITTED,ACCEPTED,REJECTED",
      aliases=("理赔材料",), group="insurance_domain"),
    C("InsuranceSurvey", "查勘", "survey_id",
      "保险公司的现场查勘记录。查勘结论是定损的基础。",
      "survey_id:id, claim_id:str, surveyor:str, "
      "surveyed_at:dt, survey_result:enum, "
      "survey_report_uri:str",
      "SCHEDULED,ON_SITE,COMPLETED,CANCELLED",
      aliases=("查勘",), group="insurance_domain"),
    C("InsuranceAssessment", "定损", "assessment_id",
      "损失金额的核定。定损金额往往低于申报金额，差异需争取。",
      "assessment_id:id, claim_id:str, assessed_amount:dec, "
      "assessment_basis:str, assessed_at:dt, "
      "disputed:bool",
      "PENDING,ASSESSED,DISPUTED,CONFIRMED",
      aliases=("定损",), group="insurance_domain"),
    C("InsurancePayout", "保险赔付", "payout_id",
      "保险公司实际支付的赔款。到账时间影响企业现金流。",
      "payout_id:id, claim_id:str, payout_amount:dec, "
      "paid_at:dt, bank_serial_no:str, "
      "deducted:dec",
      "PENDING,PAID,ARRIVED,PARTIAL",
      aliases=("赔款",), group="insurance_domain"),
    C("InsuranceRejection", "拒赔", "rejection_id",
      "保险公司拒赔的记录。拒赔理由需逐条核对保单条款。",
      "rejection_id:id, claim_id:str, reject_reason:enum, "
      "reject_detail:txt, rejected_at:dt, "
      "appealed:bool",
      "RECORDED,APPEALED,FINAL",
      aliases=("拒赔",), group="insurance_domain"),
    C("InsuranceRenewal", "续保", "renewal_id",
      "保单续保记录。脱保期间发生的事故不予赔付。",
      "renewal_id:id, policy_id:str, new_policy_id:str, "
      "renewed_at:dt, premium_diff:dec",
      "REMINDED,RENEWED,LAPSED",
      aliases=("续保",), group="insurance_domain"),
    C("InsuranceBroker", "保险经纪", "broker_id",
      "提供保险方案的外部经纪。费率与方案优劣直接影响成本。",
      "broker_id:id, broker_name:str, license_no:str, "
      "cooperation_since:date, rating:num, "
      "status:enum",
      "ACTIVE,SUSPENDED,TERMINATED",
      aliases=("经纪",), group="insurance_domain"),
    C("InsuranceEndorsement", "批单", "endorsement_id",
      "保单内容的变更批单。增减保额、变更被保险人都会出批单。",
      "endorsement_id:id, policy_id:str, endorsement_no:str, "
      "change_content:json, effective_at:dt, "
      "premium_delta:dec",
      "APPLIED,EFFECTIVE,REJECTED",
      aliases=("批单",), group="insurance_domain"),
    C("InsuranceDeductible", "免赔额", "deductible_id",
      "每次事故的免赔额度。小额事故可能全额自担。",
      "deductible_id:id, policy_id:str, deductible_amount:dec, "
      "deductible_mode:enum, applies_to:str",
      "ACTIVE,EXPIRED", aliases=("免赔",), group="insurance_domain"),
    C("InsuranceSubrogation", "代位求偿", "subrogation_id",
      "保险公司赔付后向责任方追偿。追偿结果影响次年费率。",
      "subrogation_id:id, claim_id:str, liable_party:enum, "
      "recovery_amount:dec, recovered_at:dt, "
      "status:enum",
      "INITIATED,NEGOTIATING,RECOVERED,ABANDONED",
      aliases=("追偿",), group="insurance_domain"),
]

# ────────────────────────────── 关务域 ──────────────────────────────
_customs = [
    C("CustomsDeclaration", "报关单", "declaration_id",
      "进出口货物的报关单。品名、税号与金额必须与实际一致，申报不实会被处罚。",
      "declaration_id:id, waybill_id:str, declaration_no:str, "
      "declare_type:enum, declare_amount:dec, "
      "declared_at:dt, released_at:dt",
      "DRAFT,DECLARED,INSPECTING,RELEASED,HELD",
      aliases=("报关",), group="customs_domain"),
    C("CustomsDocument", "关务单证", "document_id",
      "报关所需的单证。发票、装箱单、合同、许可证缺一不可。",
      "document_id:id, declaration_id:str, document_type:enum, "
      "file_uri:str, verified:bool, "
      "uploaded_at:dt",
      "MISSING,UPLOADED,VERIFIED,REJECTED",
      aliases=("关务单证",), group="customs_domain"),
    C("CustomsDuty", "关税", "duty_id",
      "进出口货物的税费。关税、增值税、消费税分别计算。",
      "duty_id:id, declaration_id:str, duty_type:enum, "
      "duty_amount:dec, tax_base:dec, "
      "paid_at:dt",
      "CALCULATED,PAID,REFUNDED,DISPUTED",
      aliases=("税费",), group="customs_domain"),
    C("CustomsInspection", "海关查验", "inspection_id",
      "海关对货物的查验。查验方式与结果直接影响放行时效。",
      "inspection_id:id, declaration_id:str, inspection_mode:enum, "
      "inspection_result:enum, inspected_at:dt, "
      "delay_hours:dec",
      "NOTIFIED,INSPECTING,PASSED,FAILED",
      aliases=("查验",), group="customs_domain"),
    C("CustomsClearance", "清关", "clearance_id",
      "货物清关的整体流程记录。清关时效是跨境运输的核心指标。",
      "clearance_id:id, declaration_id:str, clearance_mode:enum, "
      "started_at:dt, cleared_at:dt, "
      "duration_hours:dec",
      "PENDING,PROCESSING,CLEARED,HELD",
      aliases=("清关",), group="customs_domain"),
    C("CustomsBroker", "报关行", "broker_id",
      "代理报关的外部机构。专业度直接影响清关时效与合规风险。",
      "broker_id:id, broker_name:str, customs_code:str, "
      "city:str, rating:num, status:enum",
      "ACTIVE,SUSPENDED,TERMINATED",
      aliases=("报关行", "代理"), group="customs_domain"),
    C("CustomsTariffCode", "税则号", "tariff_code_id",
      "商品的税则号列。税号归类错误会导致税率错误与处罚。",
      "tariff_code_id:id, code:str, code_description:str, "
      "duty_rate:dec, unit:str, "
      "effective_from:date",
      "ACTIVE,EXPIRED", aliases=("税号", "HS编码"), group="customs_domain"),
    C("CustomsQuota", "配额", "quota_id",
      "进出口配额管理。配额用尽后需申请或转其他贸易方式。",
      "quota_id:id, quota_type:enum, total_quota:dec, "
      "used_quota:dec, period:str, "
      "expire_at:date",
      "ACTIVE,EXHAUSTED,EXPIRED",
      aliases=("配额",), group="customs_domain"),
    C("CustomsBond", "保证金", "bond_id",
      "海关事务担保的保证金。保证金未退会占用资金。",
      "bond_id:id, declaration_id:str, bond_amount:dec, "
      "paid_at:dt, refunded_at:dt, "
      "refund_status:enum",
      "PAID,REFUNDING,REFUNDED,FORFEITED",
      aliases=("保证金",), group="customs_domain"),
    C("CustomsTransit", "转关", "transit_id",
      "货物在关境内的转运。转关需办理转关单并接受监管。",
      "transit_id:id, declaration_id:str, from_customs:str, "
      "to_customs:str, transit_started_at:dt, "
      "transit_ended_at:dt",
      "APPLIED,APPROVED,IN_TRANSIT,ARRIVED,CLOSED",
      aliases=("转关",), group="customs_domain"),
    C("CustomsManifest", "舱单", "manifest_id",
      "运输工具的载货清单。舱单与报关单不一致会被布控。",
      "manifest_id:id, transport_id:str, manifest_no:str, "
      "item_count:int, total_weight_kg:dec, "
      "submitted_at:dt",
      "DRAFT,SUBMITTED,MATCHED,MISMATCH",
      aliases=("舱单",), group="customs_domain"),
    C("CustomsViolation", "关务违规", "violation_id",
      "申报不实或违规行为。处罚记录影响企业信用等级。",
      "violation_id:id, declaration_id:str, violation_type:enum, "
      "penalty_amount:dec, recorded_at:dt, "
      "credit_impact:bool",
      "RECORDED,APPEALED,CONFIRMED,CLOSED",
      aliases=("违规",), group="customs_domain"),
    C("CustomsRefund", "退税", "refund_id",
      "出口退税申请与到账。退税率与申报时效直接影响利润。",
      "refund_id:id, declaration_id:str, refund_amount:dec, "
      "applied_at:dt, refunded_at:dt, "
      "refund_rate:dec",
      "APPLIED,REVIEWING,APPROVED,REFUNDED,REJECTED",
      aliases=("退税",), group="customs_domain"),
    C("CustomsAEOCert", "AEO认证", "aeo_id",
      "海关高级认证企业资质。AEO 企业查验率低、通关快。",
      "aeo_id:id, entity_id:str, cert_level:enum, "
      "cert_no:str, issue_date:date, "
      "expiry_date:date",
      "APPLIED,CERTIFIED,SUSPENDED,REVOKED",
      aliases=("AEO",), group="customs_domain"),
]

# ────────────────────────────── 合规域 ──────────────────────────────
_compliance = [
    C("ComplianceRule", "合规规则", "rule_id",
      "业务必须遵守的合规条款。法规、行业标准与内部制度三层来源。",
      "rule_id:id, rule_code:str, rule_name:str, "
      "rule_source:enum, legal_basis:str, "
      "check_frequency:enum, active:bool",
      "DRAFT,ACTIVE,DEPRECATED", aliases=("合规条款",), group="compliance_domain"),
    C("ComplianceCheck", "合规检查", "check_id",
      "按规则执行的检查记录。检查项、检查人与结论三段齐全才有效。",
      "check_id:id, rule_id:str, subject_type:enum, "
      "subject_id:str, check_items:json, "
      "check_result:enum, checked_at:dt",
      "PLANNED,PASSED,FAILED,WAIVED",
      aliases=("合规检查",), group="compliance_domain"),
    C("ComplianceViolation", "违规记录", "violation_id",
      "违反合规要求的事实记录。违规等级决定处罚力度与整改要求。",
      "violation_id:id, rule_id:str, subject_id:str, "
      "violation_level:enum, violation_desc:txt, "
      "found_at:dt, penalty_amount:dec",
      "FOUND,CONFIRMED,APPEALED,CLOSED",
      aliases=("违规",), group="compliance_domain"),
    C("ComplianceRectification", "整改", "rectification_id",
      "违规后的整改要求与完成情况。整改未闭环的不得解除限制。",
      "rectification_id:id, violation_id:str, requirement:txt, "
      "due_date:date, completed_date:date, "
      "verified_by:str",
      "ISSUED,IN_PROGRESS,COMPLETED,VERIFIED,OVERDUE",
      aliases=("整改",), group="compliance_domain"),
    C("ComplianceTraining", "合规培训", "training_id",
      "合规培训的组织与完成记录。特定岗位必须持培训合格证上岗。",
      "training_id:id, training_type:enum, target_roles:json, "
      "trained_count:int, pass_rate:dec, "
      "trained_at:date",
      "PLANNED,ONGOING,COMPLETED,FAILED",
      aliases=("合规培训",), group="compliance_domain"),
    C("ComplianceLicense", "经营许可", "license_id",
      "企业经营所需的许可证。道路运输经营许可证是承运的前置条件。",
      "license_id:id, entity_id:str, license_type:enum, "
      "license_no:str, issue_date:date, expiry_date:date",
      "APPLIED,VALID,EXPIRING,EXPIRED,REVOKED",
      aliases=("许可证",), group="compliance_domain"),
    C("ComplianceAudit", "合规审计", "audit_id",
      "外部或内部的合规审计。审计发现的问题须限期整改。",
      "audit_id:id, audit_type:enum, audit_scope:str, "
      "auditor:str, findings:json, "
      "audited_at:dt",
      "PLANNED,ONGOING,COMPLETED,FOLLOW_UP",
      aliases=("合规审计",), group="compliance_domain"),
    C("ComplianceRisk", "合规风险台账", "risk_id",
      "识别出的合规风险项。风险等级与应对措施构成风险台账。",
      "risk_id:id, risk_item_code:str, risk_desc:txt, "
      "risk_level:enum, mitigation:txt, "
      "owner_id:str, review_date:date",
      "IDENTIFIED,ASSESSED,MITIGATING,CLOSED",
      aliases=("风险项",), group="compliance_domain"),
    C("ComplianceSanctionScreen", "制裁筛查", "screen_id",
      "对合作方是否在制裁名单的筛查。命中即禁止合作。",
      "screen_id:id, subject_id:str, screen_list:enum, "
      "screen_result:enum, screened_at:dt, "
      "hit_detail:txt",
      "PASSED,HIT,FALSE_POSITIVE,PENDING",
      aliases=("制裁筛查",), group="compliance_domain"),
    C("ComplianceDangerousGoods", "危运合规", "dg_compliance_id",
      "危险品运输的合规检查。资质、包装、标识、路线四项必查。",
      "dg_compliance_id:id, waybill_id:str, check_items:json, "
      "compliance_result:enum, checked_at:dt, "
      "corrective_action:txt",
      "CHECKED,COMPLIANT,NON_COMPLIANT,RECTIFIED",
      aliases=("危运合规",), group="compliance_domain"),
    C("ComplianceOverload", "超载治理", "overload_id",
      "超载识别与处理记录。超载是法规红线，处罚重且影响保险理赔。",
      "overload_id:id, waybill_id:str, rated_load_t:dec, "
      "actual_load_t:dec, overload_pct:dec, "
      "handled_mode:enum, penalty_amount:dec",
      "DETECTED,HANDLING,HANDLED,RELEASED",
      aliases=("超载",), group="compliance_domain"),
    C("ComplianceDriverHours", "驾驶时长合规", "hours_id",
      "连续驾驶与累计驾驶时长的合规监控。疲劳驾驶是重大事故主因。",
      "hours_id:id, driver_id:str, waybill_id:str, "
      "continuous_hours:dec, daily_hours:dec, "
      "rest_minutes:int, compliant:bool",
      "MONITORING,COMPLIANT,WARNING,VIOLATED",
      aliases=("驾驶时长", "疲劳驾驶"), group="compliance_domain"),
    C("ComplianceDataPrivacy", "数据隐私", "privacy_id",
      "个人信息处理的合规记录。收集、使用、共享、删除各环节都需授权。",
      "privacy_id:id, data_subject_type:enum, "
      "processing_purpose:enum, consent_obtained:bool, "
      "retention_days:int, recorded_at:dt",
      "RECORDED,CONSENTED,WITHDRAWN,ERASED",
      aliases=("隐私合规",), group="compliance_domain"),
    C("ComplianceReport", "合规报告", "report_id",
      "定期向监管或管理层提交的合规报告。报告数据必须可溯源。",
      "report_id:id, report_period:str, report_type:enum, "
      "issue_count:int, rectified_count:int, "
      "submitted_at:dt",
      "DRAFT,SUBMITTED,ACCEPTED,RETURNED",
      aliases=("合规报告",), group="compliance_domain"),
    C("ComplianceCertification", "体系认证", "certification_id",
      "ISO 等体系认证。投标时常常是硬门槛。",
      "certification_id:id, entity_id:str, cert_type:enum, "
      "cert_no:str, issue_date:date, expiry_date:date, "
      "cert_body:str",
      "APPLIED,CERTIFIED,EXPIRING,EXPIRED",
      aliases=("认证",), group="compliance_domain"),
]

_links = [
    L("evidence_packaged_by", "证据包", "Evidence", "EvidencePackage", "many_to_one"),
    L("evidence_linked_in_chain", "证据链", "Evidence", "EvidenceChain", "many_to_one", temporal=True),
    L("evidence_has_photo", "照片证据", "Evidence", "EvidencePhoto", "one_to_many"),
    L("evidence_has_video", "视频证据", "Evidence", "EvidenceVideo", "one_to_many"),
    L("evidence_has_audio", "录音证据", "Evidence", "EvidenceAudio", "one_to_many"),
    L("evidence_has_document", "文档证据", "Evidence", "EvidenceDocument", "one_to_many"),
    L("evidence_has_signature", "电子签名", "Evidence", "EvidenceSignature", "one_to_one"),
    L("evidence_has_timestamp", "时间戳", "Evidence", "EvidenceTimestamp", "one_to_one"),
    L("evidence_has_hash", "哈希存证", "Evidence", "EvidenceHash", "one_to_one"),
    L("evidence_from_source", "证据来源", "Evidence", "EvidenceSource", "many_to_one"),
    L("evidence_judged_validity", "有效性判定", "Evidence", "EvidenceValidity", "one_to_one"),
    L("evidence_tamper_checked", "防篡改校验", "Evidence", "EvidenceTamperCheck", "one_to_many", temporal=True),
    L("evidence_collected_by_task", "取证任务", "Evidence", "EvidenceCollectionTask", "many_to_one"),
    L("evidence_has_retention", "证据留存", "Evidence", "EvidenceRetention", "one_to_one"),
    L("evidence_accessed_via", "证据访问", "Evidence", "EvidenceAccess", "one_to_many", temporal=True),

    L("exception_classified_as", "异常分类", "ExceptionTicket", "ExceptionCategory", "many_to_one"),
    L("exception_from_report", "异常上报", "ExceptionTicket", "ExceptionReport", "one_to_one"),
    L("exception_investigated_by", "异常调查", "ExceptionTicket", "ExceptionInvestigation", "one_to_many"),
    L("exception_attributed_to", "责任归因", "ExceptionTicket", "ExceptionAttribution", "one_to_one"),
    L("exception_resolved_by", "异常处理", "ExceptionTicket", "ExceptionResolution", "one_to_many"),
    L("exception_escalated_by", "异常升级", "ExceptionTicket", "ExceptionEscalation", "one_to_many", temporal=True),
    L("exception_closed_by", "异常关闭", "ExceptionTicket", "ExceptionClosure", "one_to_one"),
    L("exception_penalized_by", "异常处罚", "ExceptionTicket", "ExceptionPenalty", "one_to_many"),
    L("exception_compensated_by", "异常赔付", "ExceptionTicket", "ExceptionCompensation", "one_to_many"),
    L("exception_claimed_by", "理赔申请", "ExceptionTicket", "ExceptionClaim", "one_to_many"),
    L("exception_has_root_cause", "根因分析", "ExceptionTicket", "ExceptionRootCause", "one_to_one"),
    L("root_cause_prevented_by", "预防措施", "ExceptionRootCause", "ExceptionPrevention", "one_to_many"),
    L("exception_bound_by_sla", "异常时效", "ExceptionTicket", "ExceptionSLA", "many_to_one"),
    L("exception_notified_via", "异常通知", "ExceptionTicket", "ExceptionNotification", "one_to_many", temporal=True),
    L("exception_reviewed_by", "异常复盘", "ExceptionTicket", "ExceptionReview", "many_to_many"),
    L("exception_measures_trend", "异常趋势", "ExceptionTicket", "ExceptionTrend", "many_to_many"),

    L("policy_from_application", "投保申请", "InsurancePolicy", "InsuranceApplication", "one_to_one"),
    L("policy_charges_premium", "保费", "InsurancePolicy", "InsurancePremium", "one_to_many"),
    L("policy_defines_coverage", "保险责任", "InsurancePolicy", "InsuranceCoverage", "one_to_many"),
    L("policy_has_claim", "理赔案件", "InsurancePolicy", "InsuranceClaim", "one_to_many", temporal=True),
    L("claim_requires_document", "理赔材料", "InsuranceClaim", "InsuranceClaimDocument", "one_to_many"),
    L("claim_surveyed_by", "查勘", "InsuranceClaim", "InsuranceSurvey", "one_to_one"),
    L("claim_assessed_by", "定损", "InsuranceClaim", "InsuranceAssessment", "one_to_one"),
    L("claim_paid_by", "保险赔付", "InsuranceClaim", "InsurancePayout", "one_to_one"),
    L("claim_rejected_by", "拒赔", "InsuranceClaim", "InsuranceRejection", "one_to_one"),
    L("policy_renewed_by", "续保", "InsurancePolicy", "InsuranceRenewal", "one_to_one"),
    L("policy_placed_by_broker", "保险经纪", "InsurancePolicy", "InsuranceBroker", "many_to_one"),
    L("policy_endorsed_by", "批单", "InsurancePolicy", "InsuranceEndorsement", "one_to_many"),
    L("policy_has_deductible", "免赔额", "InsurancePolicy", "InsuranceDeductible", "one_to_one"),
    L("claim_initiates_subrogation", "代位求偿", "InsuranceClaim", "InsuranceSubrogation", "one_to_one"),

    L("waybill_needs_declaration", "报关单", "Waybill", "CustomsDeclaration", "one_to_many"),
    L("declaration_requires_document", "关务单证", "CustomsDeclaration", "CustomsDocument", "one_to_many"),
    L("declaration_charges_duty", "关税", "CustomsDeclaration", "CustomsDuty", "one_to_many"),
    L("declaration_inspected_by", "海关查验", "CustomsDeclaration", "CustomsInspection", "one_to_many"),
    L("declaration_cleared_by", "清关", "CustomsDeclaration", "CustomsClearance", "one_to_one"),
    L("declaration_filed_by_broker", "报关行", "CustomsDeclaration", "CustomsBroker", "many_to_one"),
    L("declaration_uses_tariff_code", "税则号", "CustomsDeclaration", "CustomsTariffCode", "many_to_one"),
    L("declaration_consumes_quota", "配额", "CustomsDeclaration", "CustomsQuota", "many_to_many"),
    L("declaration_has_bond", "保证金", "CustomsDeclaration", "CustomsBond", "one_to_one"),
    L("declaration_in_transit", "转关", "CustomsDeclaration", "CustomsTransit", "one_to_many"),
    L("declaration_has_manifest", "舱单", "CustomsDeclaration", "CustomsManifest", "one_to_one"),
    L("declaration_has_violation", "关务违规", "CustomsDeclaration", "CustomsViolation", "one_to_many"),
    L("declaration_claims_refund", "退税", "CustomsDeclaration", "CustomsRefund", "one_to_one"),
    L("entity_holds_aeo", "AEO认证", "CustomsDeclaration", "CustomsAEOCert", "many_to_many"),

    L("compliance_governed_by_rule", "合规规则", "ComplianceCheck", "ComplianceRule", "many_to_one"),
    L("compliance_checked_via", "合规检查", "ExceptionTicket", "ComplianceCheck", "many_to_many"),
    L("compliance_has_violation", "违规记录", "ComplianceCheck", "ComplianceViolation", "one_to_many"),
    L("violation_requires_rectification", "整改", "ComplianceViolation", "ComplianceRectification", "one_to_one"),
    L("compliance_needs_training", "合规培训", "ComplianceViolation", "ComplianceTraining", "many_to_many"),
    L("entity_holds_license", "经营许可", "Carrier", "ComplianceLicense", "one_to_many"),
    L("compliance_audited_by", "合规审计", "ComplianceCheck", "ComplianceAudit", "many_to_many"),
    L("compliance_has_risk", "风险台账", "ComplianceCheck", "ComplianceRisk", "one_to_many"),
    L("counterparty_screened_by", "制裁筛查", "Carrier", "ComplianceSanctionScreen", "one_to_many", temporal=True),
    L("waybill_checked_dg_compliance", "危运合规", "Waybill", "ComplianceDangerousGoods", "one_to_many"),
    L("waybill_checked_overload", "超载治理", "Waybill", "ComplianceOverload", "one_to_many"),
    L("driver_monitored_hours", "驾驶时长合规", "Driver", "ComplianceDriverHours", "one_to_many", temporal=True),
    L("privacy_governs_data", "数据隐私", "ComplianceRule", "ComplianceDataPrivacy", "one_to_many"),
    L("compliance_publishes_report", "合规报告", "ComplianceCheck", "ComplianceReport", "many_to_many"),
    L("entity_holds_certification", "体系认证", "Carrier", "ComplianceCertification", "one_to_many"),
]

DOMAINS.extend([
    {"id": "evidence_domain", "label": "证据域", "classes": _evidence},
    {"id": "exception_domain", "label": "异常域", "classes": _exception},
    {"id": "insurance_domain", "label": "保险域", "classes": _insurance},
    {"id": "customs_domain", "label": "关务域", "classes": _customs},
    {"id": "compliance_domain", "label": "合规域", "classes": _compliance},
])

CLASSES = [c for d in DOMAINS for c in d["classes"]]
LINKS = _links
