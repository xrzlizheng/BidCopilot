# BidCopilot 数据库参考

> 口径：本文档生成于 2026-10-24（当时 head `0038_p3e_scoped_roles`、53 张表）；其余章节仍按该基线描述。**当前口径（2026-11-19 社区版实测）**：head `0077_price_quotes`、业务表 **78 张**（模块总览见上，双向核对一致；`tenant_plans` 套餐表属企业版，社区版链不含）。
> 0039–0077 增量要点：合同运营 5 表 + 模板版本/法条（0054–0055）、`intel_watchlist`（0058）、`submission_receipts(+attachments)`/`notification_channels`/`ai_usage_ledger`/`api_tokens`/`webhook_endpoints(+deliveries)`（0059–0067）、`opportunity_matches`（自动匹配）、`contract_review_templates(+versions)`/`law_articles`/`contract_records(+draft/approvals/ledger/changes)`、`cost_ledger.order_no`、`bid_decision_runs` 约束、`price_strategies`（0052）与 `price_quotes`（0077）；**0068 已删除 clients/client_members 及各表 client_id 列**。上述新表逐列以迁移文件与 `backend/app/modules/*/models.py` 为准，本文分模块明细基线仍为 2026-10-24。
> 逐列类型/可空/默认值的完整清单以源码为准（`backend/app/modules/*/models.py`）；本文聚焦表用途、约束、索引、枚举与租户隔离形态——即结构维护与升级最常查的部分。
> 姊妹文档：[详细设计文档](2026-10-24-bidcopilot-detailed-design.md) §3 数据层设计。编写日期：2026-10-24。

## 通用约定

- 所有业务表主键为 UUID（应用层 `uuid4` 生成，非 DB 序列）；`created_at` 双兜底（Python `datetime.now(UTC)` + SQL `server_default now()`）。
- 租户隔离：44 张表经 `TenantMixin` 带 `tenant_id`（FK→tenants ON DELETE CASCADE + 索引）；8 张显式声明 `tenant_id`（`users`/`roles`/`user_roles`/`knowledge_packs`/`knowledge_documents`/`knowledge_chunks`/`knowledge_images`/`audit_events`）；仅 `tenants` 本体无 `tenant_id`。业务查询必须经 `TenantRepository.scoped()` 追加租户谓词。
- 外键共 143 个（135 内联 + 8 迁移补挂，其中 4 个为 0034 循环依赖、4 个为 0029 知识库补挂）。
- 4 个**部分唯一索引**（迁移 `postgresql_where`，看模型 `__table_args__` 会漏）：
  - `roles.uq_roles_system_code (code) WHERE tenant_id IS NULL`——系统角色全局唯一
  - `model_configs.uq_model_configs_tenant_purpose WHERE scope='tenant'`、`uq_model_configs_personal_purpose WHERE scope='personal'`
  - `cost_ledger.uq_cost_ledger_tenant_order (tenant_id, order_no) WHERE order_no IS NOT NULL`（迁移 `0073_cost_ledger_order.py`）——同一网关 orderNo 只入账一次；升级前自动预检重复 `(tenant_id, order_no)`，存在重复即中止并要求人工清理，不自动改账
- 列名与属性名不一致一处：`source_sync_runs` 的 DB 列为 `warnings_json`，模型属性为 `warnings`。
- 迁移只前进；共享环境禁止 destructive downgrade（所有 `drop_*` 均在 `downgrade()` 内）。

## 模块总览（78 表 / 18 模块，2026-11-19 社区版实测）

| 模块 | 表 | 数量 |
|---|---|---|
| identity | tenants / users / roles / user_roles / api_tokens | 5 |
| org | org_units / org_members | 2 |
| access | resource_acl | 1 |
| audit | audit_events | 1 |
| opportunities | source_sync_runs / opportunities / opportunity_snapshots / opportunity_favorites / opportunity_subscriptions / opportunity_buyer_intel / opportunity_buyer_watchlist / opportunity_matches / award_records | 9 |
| assets | companies / company_qualifications / company_personnel / company_performances / company_credits / company_bids / field_policies | 7 |
| projects | bid_projects / tender_documents / tender_constraints / scoring_items / response_matrix / bid_results / compliance_responses / parse_jobs / bid_decision_runs / bid_commitments / project_readiness_items / project_reminders / submission_receipts / submission_receipt_attachments | 14 |
| generation | generation_runs / bid_sections / evidence_links | 3 |
| evaluation | bid_evaluations | 1 |
| compliance | compliance_reports / findings / policies / compliance_access_requests | 4 |
| contracts | contract_reviews / contract_clauses / contract_findings / contract_milestones / tenant_contract_rules / contract_review_templates / contract_template_versions / law_articles / contract_records / contract_draft_clauses / contract_approvals / contract_ledger_entries / contract_changes | 13 |
| knowledge | knowledge_bases / knowledge_packs / knowledge_documents / knowledge_chunks / knowledge_images | 5 |
| costs | cost_ledger / paid_call_quotas / ai_usage_ledger | 3 |
| notifications | notifications / notification_channels / webhook_endpoints / webhook_deliveries | 4 |
| system | model_configs / secret_store / tenant_settings | 3 |
| company_intel | intel_watchlist | 1 |
| pricing | price_strategies / price_quotes | 2 |

合计 **78 张业务表**（不含 `alembic_version`；社区版口径，`tenant_plans` 属企业版），与 `information_schema.tables` 及模型 `__tablename__` 双向核对一致。refresh 会话为无状态 JWT Cookie，**不存在 refresh_tokens 表**。



## 模块 `access`


### `resource_acl`

- 模型类：`ResourceAcl`（`backend/app/modules/access/models.py`）
- 创建迁移：`0029_p1_knowledge_acl.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：资源级 ACL：对任意 resource_type/resource_id 授予 user/tenant/org_unit 主体角色，支持到期与后代继承。
- 列（11）：`id` `tenant_id` `resource_type` `resource_id` `subject_type` `subject_id` `role` `include_descendants` `granted_by` `expires_at` `created_at`
- 约束与索引：
  UNIQUE `uq_resource_acl_subject`（`tenant_id`, `resource_type`, `resource_id`, `subject_type`, `subject_id`）
  INDEX `ix_resource_acl_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_resource_acl_tenant_resource`（`tenant_id`, `resource_type`, `resource_id`）
  INDEX `ix_resource_acl_tenant_subject`（`tenant_id`, `subject_type`, `subject_id`, `resource_type`）

## 模块 `assets`


### `companies`

- 模型类：`Company`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0003_p0_assets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司/客户主档：tenant+name 唯一，credit_code 建索引，contacts 存联系方式 JSONB。
- 列（11）：`id` `tenant_id` `name` `credit_code` `region` `industry` `contacts` `source` `source_ref` `fetched_at` `created_at`
- 约束与索引：
  UNIQUE `uq_companies_tenant_name`（`tenant_id`, `name`）
  INDEX `ix_companies_credit_code`（`credit_code`）（迁移生成，模型侧为列级 index）
  INDEX `ix_companies_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `company_qualifications`

- 模型类：`Qualification`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0003_p0_assets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司资质证书：有效期 valid_from/valid_to，valid_to 建索引支撑临期提醒。
- 列（13）：`id` `tenant_id` `company_id` `org_unit_id` `dedup_key` `name` `level` `cert_no` `issuer` `valid_from` `valid_to` `source` `created_at`
- 约束与索引：
  UNIQUE `uq_qualifications_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_company_qualifications_company_id`（`company_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_qualifications_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_qualifications_valid_to`（`valid_to`）（迁移生成，模型侧为列级 index）
  INDEX `ix_qualifications_tenant_org`（`tenant_id`, `org_unit_id`）

### `company_personnel`

- 模型类：`Personnel`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0003_p0_assets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司人员证书：cert_type/level/major/cert_no，valid_to 建索引。
- 列（13）：`id` `tenant_id` `company_id` `org_unit_id` `dedup_key` `name` `cert_type` `level` `major` `cert_no` `valid_to` `source` `created_at`
- 约束与索引：
  UNIQUE `uq_personnel_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_company_personnel_company_id`（`company_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_personnel_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_personnel_valid_to`（`valid_to`）（迁移生成，模型侧为列级 index）
  INDEX `ix_personnel_tenant_org`（`tenant_id`, `org_unit_id`）

### `company_performances`

- 模型类：`Performance`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0003_p0_assets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司业绩：项目名、金额、完成日期、采购方、类别。
- 列（12）：`id` `tenant_id` `company_id` `org_unit_id` `dedup_key` `project_name` `amount` `complete_date` `buyer` `category` `source` `created_at`
- 约束与索引：
  UNIQUE `uq_performances_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_company_performances_company_id`（`company_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_performances_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_performances_tenant_org`（`tenant_id`, `org_unit_id`）

### `company_credits`

- 模型类：`Credit`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0003_p0_assets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司信用核查项：check_item+status+checked_at，用于资质匹配时的信用异常判定。
- 列（10）：`id` `tenant_id` `company_id` `org_unit_id` `dedup_key` `check_item` `status` `checked_at` `source` `created_at`
- 约束与索引：
  UNIQUE `uq_credits_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_company_credits_company_id`（`company_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_credits_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_credits_tenant_org`（`tenant_id`, `org_unit_id`）

### `company_bids`

- 模型类：`CompanyBid`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0007_p1_company_bids.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：公司招投标流水：按 role(tenderer/winner/agency) 记录该公司参与或中标的公告，tenant+dedup_key 唯一。
- 列（21）：`id` `tenant_id` `company_id` `role` `dedup_key` `source` `source_id` `title` `project_name` `project_code` `buyer` `agency` `winner` `budget_amount` `win_bid_amount` `publish_at` `info_type` `source_url` `fetched_at` `raw_snapshot_ref` `created_at`
- 约束与索引：
  UNIQUE `uq_company_bids_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_company_bids_company_id`（`company_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_bids_tenant_company_publish`（`tenant_id`, `company_id`, `publish_at`）（迁移生成，模型侧为列级 index）
  INDEX `ix_company_bids_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `field_policies`

- 模型类：`FieldPolicy`（`backend/app/modules/assets/models.py`）
- 创建迁移：`0036_p3b_field_policies.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：字段级脱敏策略：按 resource_type+field_name 配置 min_clearance 与 mask_strategy，命中即对输出脱敏。
- 列（8）：`id` `tenant_id` `resource_type` `field_name` `min_clearance` `mask_strategy` `updated_by` `created_at`
- 约束与索引：
  UNIQUE `uq_field_policies_field`（`tenant_id`, `resource_type`, `field_name`）
  INDEX `ix_field_policies_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

## 模块 `audit`


### `audit_events`

- 模型类：`AuditEvent`（`backend/app/modules/audit/models.py`）
- 创建迁移：`0001_p0_identity.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：审计事件流水：seq 自增、prev_hash/hash 构成哈希链；跨租户操作以 target_tenant_id 记录被操作租户。
- 列（12）：`id` `seq` `tenant_id` `target_tenant_id` `actor_id` `action` `resource_type` `resource_id` `data` `prev_hash` `hash` `created_at`
- 约束与索引：
  INDEX `ix_audit_events_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_audit_events_tenant_seq`（`tenant_id`, `seq`）

## 模块 `compliance`


### `compliance_reports`

- 模型类：`ComplianceReport`（`backend/app/modules/compliance/models.py`）
- 创建迁移：`0006_p3_compliance.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合规审查报告：一次审查一份报告，rule_version 记录规则集版本，summary_json 存汇总。
- 列（9）：`id` `tenant_id` `project_id` `status` `rule_version` `file_hash` `summary_json` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_compliance_reports_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_compliance_reports_tenant_project`（`tenant_id`, `project_id`）

### `findings`

- 模型类：`Finding`（`backend/app/modules/compliance/models.py`）
- 创建迁移：`0006_p3_compliance.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合规问题项：report 下的规则命中明细，含 expected/actual/basis/suggestion、auto_fixable、needs_review 与 status。
- 列（19）：`id` `tenant_id` `project_id` `report_id` `rule_id` `category` `severity` `finding_type` `message` `location` `expected` `actual` `basis` `suggestion` `auto_fixable` `needs_review` `status` `evidence_ref` `created_at`
- 约束与索引：
  INDEX `ix_findings_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_findings_tenant_report`（`tenant_id`, `report_id`）

### `policies`

- 模型类：`CompliancePolicy`（`backend/app/modules/compliance/models.py`）
- 创建迁移：`0014_p3_policies.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合规规则开关：按 rule_id 覆盖启用状态、严重级别与阈值。
- 列（8）：`id` `tenant_id` `rule_id` `enabled` `severity_override` `threshold_override` `updated_by` `created_at`
- 约束与索引：
  UNIQUE `uq_policies_tenant_rule`（`tenant_id`, `rule_id`）
  INDEX `ix_policies_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `compliance_access_requests`

- 模型类：`ComplianceAccessRequest`（`backend/app/modules/compliance/models.py`）
- 创建迁移：`0033_p2b_break_glass.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：破窗访问申请（break-glass）：申请人在限时内访问他人资源的审批流，requester/approver 双外键。
- 列（12）：`id` `tenant_id` `resource_type` `resource_id` `requester_id` `approver_id` `reason` `status` `requested_minutes` `expires_at` `decided_at` `created_at`
- 约束与索引：
  INDEX `ix_compliance_access_requests_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_compliance_access_requests_tenant_requester`（`tenant_id`, `requester_id`）
  INDEX `ix_compliance_access_requests_tenant_status`（`tenant_id`, `status`）

## 模块 `contracts`


### `contract_reviews`

- 模型类：`ContractReview`（`backend/app/modules/contracts/models.py`）
- 创建迁移：`0024_p3_contracts.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合同审查主记录：同项目同 file_hash 唯一；条款已结构化入 contract_clauses，原始字节不落库。
- 列（10）：`id` `tenant_id` `project_id` `filename` `size_bytes` `file_hash` `status` `summary_json` `created_by` `created_at`
- 约束与索引：
  UNIQUE `uq_contract_reviews_hash`（`tenant_id`, `project_id`, `file_hash`）
  INDEX `ix_contract_reviews_tenant_project`（`tenant_id`, `project_id`）

### `contract_clauses`

- 模型类：`ContractClause`（`backend/app/modules/contracts/models.py`）
- 创建迁移：`0024_p3_contracts.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合同条款：review+seq 定位，category 为受控类目（付款/违约/价格等）。
- 列（10）：`id` `tenant_id` `review_id` `seq` `title` `text` `category` `page` `source_span` `created_at`
- 约束与索引：
  INDEX `ix_contract_clauses_review`（`review_id`, `seq`）

### `contract_findings`

- 模型类：`ContractFinding`（`backend/app/modules/contracts/models.py`）
- 创建迁移：`0024_p3_contracts.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合同审查结论：pending/confirmed/dismissed 待确认语义，confirmed_by/at 落确认人。
- 列（15）：`id` `tenant_id` `review_id` `clause_id` `kind` `rule_id` `severity` `message` `legal_basis` `evidence_span` `source` `status` `confirmed_by` `confirmed_at` `created_at`
- 约束与索引：
  INDEX `ix_contract_findings_tenant_status`（`tenant_id`, `status`）

### `contract_milestones`

- 模型类：`ContractMilestone`（`backend/app/modules/contracts/models.py`）
- 创建迁移：`0025_p3_contract_ops.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：合同履约台账节点：付款/交付/质保/验收，调度器按 due_at 到期提醒并置 due。
- 列（10）：`id` `tenant_id` `review_id` `kind` `title` `due_at` `amount` `status` `reminded_at` `created_at`
- 约束与索引：
  INDEX `ix_contract_milestones_due`（`tenant_id`, `status`, `due_at`）

### `tenant_contract_rules`

- 模型类：`TenantContractRule`（`backend/app/modules/contracts/models.py`）
- 创建迁移：`0025_p3_contract_ops.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：租户自定义审查规则：正则 pattern 命中即产 finding，优先于内置规则。
- 列（9）：`id` `tenant_id` `name` `pattern` `severity` `message_template` `enabled` `created_by` `created_at`
- 约束与索引：
  UNIQUE `uq_tenant_contract_rules_name`（`tenant_id`, `name`）

## 模块 `costs`


### `cost_ledger`

- 模型类：`CostLedger`（`backend/app/modules/costs/models.py`）
- 创建迁移：`0011_p1_cost_ledger.py`
- 唯一索引迁移：`0073_cost_ledger_order.py`（部分唯一索引 + 升级前重复预检，见通用约定与附录 A.2）
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：外部数据调用成本台账：按 pcode/operation 记录网关调用、行数、订单号与是否计费。
- 列（13）：`id` `tenant_id` `pcode` `operation` `status` `order_no` `charged` `rows` `query_hash` `resource_type` `resource_id` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_cost_ledger_tenant_created`（`tenant_id`, `created_at`）
  INDEX `ix_cost_ledger_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_cost_ledger_tenant_pcode`（`tenant_id`, `pcode`）
  UNIQUE `uq_cost_ledger_tenant_order`（`tenant_id`, `order_no`）WHERE `order_no IS NOT NULL`——迁移 `0073_cost_ledger_order.py` 创建；升级前预检重复 `(tenant_id, order_no)`，存在重复组则抛 `RuntimeError` 列出 `tenant_id/order_no/count/first_created_at` 并中止，须人工清理后重跑（财务账本禁止自动清理）

## 模块 `evaluation`


### `bid_evaluations`

- 模型类：`BidEvaluation`（`backend/app/modules/evaluation/models.py`）
- 创建迁移：`0010_p3_bid_evaluations.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：投标评估任务：模型评估结果 result_json + prompt_version + 错误信息。
- 列（12）：`id` `tenant_id` `project_id` `status` `model` `prompt_version` `result_json` `error_code` `error_message` `created_by` `finished_at` `created_at`
- 约束与索引：
  INDEX `ix_bid_evaluations_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_bid_evaluations_tenant_project`（`tenant_id`, `project_id`）

## 模块 `generation`


### `generation_runs`

- 模型类：`GenerationRun`（`backend/app/modules/generation/models.py`）
- 创建迁移：`0005_p2_generation.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：标书生成批次：mode(rules/llm) + 进度计数 + 起止时间。
- 列（13）：`id` `tenant_id` `project_id` `mode` `status` `sections_total` `sections_generated` `error_code` `error_message` `created_by` `started_at` `finished_at` `created_at`
- 约束与索引：
  INDEX `ix_generation_runs_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_generation_runs_tenant_project`（`tenant_id`, `project_id`）

### `bid_sections`

- 模型类：`BidSection`（`backend/app/modules/generation/models.py`）
- 创建迁移：`0005_p2_generation.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：标书章节：project+order_index 唯一，content_json/content_text 双形态，factcheck_status+traceability_rate 记录事实核查。
- 列（18）：`id` `tenant_id` `project_id` `run_id` `scoring_item_id` `title` `section_type` `order_index` `content_json` `content_text` `status` `generation_mode` `factcheck_status` `traceability_rate` `unsourced_count` `approved_at` `approved_by` `created_at`
- 约束与索引：
  UNIQUE `uq_bid_sections_project_order`（`project_id`, `order_index`）
  INDEX `ix_bid_sections_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_bid_sections_tenant_project`（`tenant_id`, `project_id`）

### `evidence_links`

- 模型类：`EvidenceLink`（`backend/app/modules/generation/models.py`）
- 创建迁移：`0005_p2_generation.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：章节论断-证据链：claim/claim_type 与 source_type/source_ref/source_quote，status 标记 sourced/unsourced。
- 列（11）：`id` `tenant_id` `project_id` `section_id` `claim` `claim_type` `status` `source_type` `source_ref` `source_quote` `created_at`
- 约束与索引：
  INDEX `ix_evidence_links_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_evidence_links_tenant_section`（`tenant_id`, `section_id`）

## 模块 `identity`


### `tenants`

- 模型类：`Tenant`（`backend/app/modules/identity/models.py`）
- 创建迁移：`0001_p0_identity.py`
- 租户隔离：**否** — 无 tenant_id 列
- 用途：租户主表：业务租户的根实体（企业版另有平台租户 kind），绝大多数业务表经 tenant_id 外键级联归属。
- 列（6）：`id` `name` `slug` `kind` `status` `created_at`

### `users`

- 模型类：`User`（`backend/app/modules/identity/models.py`）
- 创建迁移：`0001_p0_identity.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：平台登录账号：邮箱+密码哈希，携带 clearance 密级；token_version 递增即使旧 token 失效。
- 列（10）：`id` `tenant_id` `email` `password_hash` `display_name` `status` `clearance` `last_login_at` `token_version` `created_at`
- 约束与索引：
  INDEX `ix_users_email`（`email`）（迁移生成，模型侧为列级 index）
  INDEX `ix_users_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `roles`

- 模型类：`Role`（`backend/app/modules/identity/models.py`）
- 创建迁移：`0001_p0_identity.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：角色定义：tenant_id 为空表示内置系统角色；permissions 为能力点列表，scope_types 限定可授权范围。
- 列（8）：`id` `tenant_id` `code` `name` `permissions` `is_system` `scope_types` `created_at`
- 约束与索引：
  UNIQUE `uq_roles_tenant_code`（`tenant_id`, `code`）
  部分唯一索引 `uq_roles_system_code`（`code`）WHERE tenant_id IS NULL
  INDEX `ix_roles_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `user_roles`

- 模型类：`UserRole`（`backend/app/modules/identity/models.py`）
- 创建迁移：`0001_p0_identity.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：用户-角色授权：带 scope_type/scope_id，支持同一角色按租户/部门等范围重复授权。
- 列（8）：`id` `tenant_id` `user_id` `role_id` `scope_type` `scope_id` `expires_at` `created_at`
- 约束与索引：
  UNIQUE `uq_user_roles_scoped`（`user_id`, `role_id`, `scope_type`, `scope_id`）
  INDEX `ix_user_roles_role_id`（`role_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_user_roles_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_user_roles_tenant_user_scope`（`tenant_id`, `user_id`, `scope_type`）（迁移生成，模型侧为列级 index）
  INDEX `ix_user_roles_user_id`（`user_id`）（迁移生成，模型侧为列级 index）

## 模块 `knowledge`


### `knowledge_bases`

- 模型类：`KnowledgeBase`（`backend/app/modules/knowledge/models.py`）
- 创建迁移：`0029_p1_knowledge_acl.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：知识库：scope/owner/org_unit 决定归属范围，classification+visibility+acl_version 控制访问。
- 列（14）：`id` `tenant_id` `scope` `owner_id` `org_unit_id` `name` `description` `classification` `visibility` `status` `owner_status` `acl_version` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_knowledge_bases_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_knowledge_bases_tenant_org_unit`（`tenant_id`, `org_unit_id`）
  INDEX `ix_knowledge_bases_tenant_owner`（`tenant_id`, `owner_id`）
  INDEX `ix_knowledge_bases_tenant_scope`（`tenant_id`, `scope`）

### `knowledge_packs`

- 模型类：`KnowledgePack`（`backend/app/modules/knowledge/models.py`）
- 创建迁移：`0013_p1_templates.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：知识包/模板：内置(is_builtin)或租户自建的结构化内容包，code 全局唯一，category 区分 structure/format。
- 列（18）：`id` `tenant_id` `code` `name` `category` `description` `content_json` `version` `is_builtin` `enabled` `scope` `owner_id` `classification` `status` `owner_status` `published_by` `published_at` `created_at`
- 约束与索引：
  UNIQUE `uq_knowledge_packs_code`（`code`）
  INDEX `ix_knowledge_packs_tenant_category`（`tenant_id`, `category`）
  INDEX `ix_knowledge_packs_tenant_category_status`（`tenant_id`, `category`, `status`）（迁移生成，模型侧为列级 index）
  INDEX `ix_knowledge_packs_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_knowledge_packs_tenant_scope_owner`（`tenant_id`, `scope`, `owner_id`）（迁移生成，模型侧为列级 index）

### `knowledge_documents`

- 模型类：`KnowledgeDocument`（`backend/app/modules/knowledge/models.py`）
- 创建迁移：`0016_p2_knowledge_base.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：知识文档：文件字节不入库，text_content 存解析后正文，file_hash 用于同租户去重。
- 列（19）：`id` `tenant_id` `kb_id` `classification` `acl_version` `company_id` `title` `kind` `filename` `content_type` `size_bytes` `file_hash` `object_ref` `status` `text_content` `tags` `error_message` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_knowledge_documents_tenant_created`（`tenant_id`, `created_at`）
  INDEX `ix_knowledge_documents_tenant_hash`（`tenant_id`, `file_hash`）
  INDEX `ix_knowledge_documents_tenant_kb`（`tenant_id`, `kb_id`）

### `knowledge_chunks`

- 模型类：`KnowledgeChunk`（`backend/app/modules/knowledge/models.py`）
- 创建迁移：`0016_p2_knowledge_base.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：知识分片：文档切块后的检索单元，keywords 走 GIN 索引支持关键词检索。
- 列（9）：`id` `tenant_id` `kb_id` `classification` `document_id` `chunk_index` `text` `heading` `keywords`
- 约束与索引：
  INDEX `ix_knowledge_chunks_keywords`（`keywords`） — USING gin
  INDEX `ix_knowledge_chunks_tenant_document`（`tenant_id`, `document_id`）
  INDEX `ix_knowledge_chunks_tenant_kb`（`tenant_id`, `kb_id`）

### `knowledge_images`

- 模型类：`KnowledgeImage`（`backend/app/modules/knowledge/models.py`）
- 创建迁移：`0016_p2_knowledge_base.py`
- 租户隔离：**是** — 显式 tenant_id 列
- 用途：知识图片：图片元数据与 object_ref，sensitive 标记敏感图，width/height 存尺寸。
- 列（18）：`id` `tenant_id` `kb_id` `classification` `company_id` `title` `caption` `tags` `filename` `content_type` `size_bytes` `file_hash` `object_ref` `width` `height` `sensitive` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_knowledge_images_tenant_created`（`tenant_id`, `created_at`）
  INDEX `ix_knowledge_images_tenant_kb`（`tenant_id`, `kb_id`）

## 模块 `notifications`


### `notifications`

- 模型类：`Notification`（`backend/app/modules/notifications/models.py`）
- 创建迁移：`0019_p2_subs_notifications.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：站内通知：按用户维度，read_at 为空即未读，局部索引加速未读查询。
- 列（10）：`id` `tenant_id` `user_id` `type` `title` `body` `resource_type` `resource_id` `read_at` `created_at`
- 约束与索引：
  INDEX `ix_notifications_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_notifications_tenant_user_created`（`tenant_id`, `user_id`, `created_at`）
  INDEX `ix_notifications_tenant_user_unread`（`tenant_id`, `user_id`） — WHERE read_at IS NULL
  INDEX `ix_notifications_user_id`（`user_id`）（迁移生成，模型侧为列级 index）

### `notification_channels`

- 模型类：`NotificationChannel`（`backend/app/modules/notifications/models.py`）
- 创建迁移：`0062_notification_channels.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE）
- 用途：租户级外部推送渠道（企业微信群机器人 Webhook / SMTP 邮件）；站内通知产生后按渠道 best-effort 广播，单渠道失败不影响其他渠道与站内通知。
- 列（8）：`id` `tenant_id` `kind` `target` `label` `enabled` `created_by` `created_at`
- 约束与索引：
  UNIQUE `uq_notification_channels`（`tenant_id`, `kind`, `target`）
- 说明：`kind` 取 `wechat`/`email`；wechat 的 target 强制 https；email 的 target 为逗号分隔收件人。SMTP 服务端参数经环境变量注入，不落库。

### `api_tokens`

- 模型类：`ApiToken`（`backend/app/modules/identity/api_token_models.py`）
- 创建迁移：`0066_api_tokens.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：开放 API 令牌。库里只存 SHA-256 哈希与展示前缀，明文仅在创建响应返回一次；任何列表/审计/日志都不出现明文。
- 列（10）：`id` `tenant_id` `name` `token_prefix` `token_hash` `scopes` `created_by` `expires_at` `last_used_at` `revoked_at` `created_at`
- 约束与索引：
  UNIQUE `ux_api_tokens_token_hash`（`token_hash`）
  INDEX `ix_api_tokens_tenant_created`（`tenant_id`, `created_at`）
- 口径：认证生效权限 = `scopes ∩ 创建者当前权限`（不放大、不继承角色）；吊销为软删除（`revoked_at`），记录保留供追溯。

### `webhook_endpoints` / `webhook_deliveries`

- 模型类：`WebhookEndpoint` / `WebhookDelivery`（`backend/app/modules/notifications/webhook_models.py`）
- 创建迁移：`0067_webhooks.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：出站 Webhook 订阅与投递留痕。`secret` 不明文入库：`secret_ref` 指向 `secret_store` 的 AES-GCM 记录；投递带 `X-BidCopilot-Signature`（HMAC-SHA256，含时间戳防重放）。
- `webhook_endpoints` 列（8）：`id` `tenant_id` `url` `secret_ref` `events` `enabled` `created_by` `created_at`
- `webhook_deliveries` 列（9）：`id` `tenant_id` `endpoint_id` `event` `status` `attempts` `response_code` `error` `created_at`
- 约束与索引：
  UNIQUE `uq_webhook_endpoints_tenant_url`（`tenant_id`, `url`）
  INDEX `ix_webhook_endpoints_tenant_enabled`（`tenant_id`, `enabled`）
  INDEX `ix_webhook_deliveries_tenant_endpoint_created`（`tenant_id`, `endpoint_id`, `created_at`）
- 口径：URL 仅允许 https 且拒绝内网/环回/链路本地（`WEBHOOK_ALLOWED_HOSTS` 可精确放行）；失败记录 `status/attempts/error` 原样留痕，不降级为成功，也不向业务主流程抛异常。

## 模块 `opportunities`


### `source_sync_runs`

- 模型类：`SourceSyncRun`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0002_p0_opportunities.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：外部数据源同步批次：记录 source/pcode/query_json、抓取与落库行数、告警、网关订单号与计费标记。
- 列（20）：`id` `tenant_id` `source` `pcode` `query_json` `status` `rows_fetched` `rows_upserted` `rows_new` `warnings_count` `warnings_json` `error_code` `error_message` `snapshot_ref` `gateway_order_no` `charged` `created_by` `started_at` `finished_at` `created_at`
- 约束与索引：
  INDEX `ix_source_sync_runs_tenant_created`（`tenant_id`, `created_at`）
  INDEX `ix_source_sync_runs_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `opportunities`

- 模型类：`Opportunity`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0002_p0_opportunities.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：商机/公告主表：外部数据源的招标与中标信息，tenant+source+dedup_key 唯一；content_html 与 content_text 分列存储。
- 列（28）：`id` `tenant_id` `source` `source_id` `dedup_key` `title` `project_code` `project_name` `buyer` `agency` `winner` `region` `industry` `budget_amount` `win_bid_amount` `publish_at` `bid_deadline` `info_type` `source_url` `detail_status` `confidence` `content_html` `content_text` `content_fetched_at` `source_updated_at` `fetched_at` `raw_snapshot_ref` `created_at`
- 约束与索引：
  UNIQUE `uq_opportunities_tenant_source_dedup`（`tenant_id`, `source`, `dedup_key`）
  INDEX `ix_opportunities_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_opportunities_tenant_info_type`（`tenant_id`, `info_type`）
  INDEX `ix_opportunities_tenant_publish`（`tenant_id`, `publish_at`）

### `opportunity_snapshots`

- 模型类：`OpportunitySnapshot`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0002_p0_opportunities.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：商机原始快照索引：raw_snapshot_ref 指向对象存储原文，content_hash 用于变更检测。
- 列（7）：`id` `tenant_id` `opportunity_id` `raw_snapshot_ref` `content_hash` `fetched_at` `created_at`
- 约束与索引：
  INDEX `ix_opportunity_snapshots_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_opportunity_snapshots_tenant_opp`（`tenant_id`, `opportunity_id`）

### `opportunity_favorites`

- 模型类：`OpportunityFavorite`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0002_p0_opportunities.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：用户商机收藏：租户+用户+商机唯一。
- 列（5）：`id` `tenant_id` `user_id` `opportunity_id` `created_at`
- 约束与索引：
  UNIQUE `uq_opportunity_favorites`（`tenant_id`, `user_id`, `opportunity_id`）
  INDEX `ix_opportunity_favorites_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_opportunity_favorites_user_id`（`user_id`）（迁移生成，模型侧为列级 index）

### `opportunity_subscriptions`

- 模型类：`OpportunitySubscription`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0019_p2_subs_notifications.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：商机订阅任务：query_json 描述检索条件，interval_minutes 控制调度频率，last_sync_run_id 指向最近一次同步。
- 列（10）：`id` `tenant_id` `user_id` `name` `query_json` `interval_minutes` `enabled` `last_run_at` `last_sync_run_id` `created_at`
- 约束与索引：
  UNIQUE `uq_opportunity_subscriptions_name`（`tenant_id`, `user_id`, `name`）
  INDEX `ix_opportunity_subscriptions_tenant_enabled`（`tenant_id`, `enabled`）
  INDEX `ix_opportunity_subscriptions_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_opportunity_subscriptions_user_id`（`user_id`）（迁移生成，模型侧为列级 index）

### `opportunity_buyer_intel`

- 模型类：`OpportunityBuyerIntel`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0020_p2_buyer_intel.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：采购方情报：单个商机的采购方历史中标、头部中标人、既有供应商等付费数据缓存，含计费与错误信息。
- 列（18）：`id` `tenant_id` `opportunity_id` `buyer_name` `status` `winner_rows` `top_winners` `existing_suppliers` `recent_wins_12m` `rows_fetched` `synced_at` `snapshot_ref` `gateway_order_no` `charged` `error_code` `error_message` `created_by` `created_at`
- 约束与索引：
  UNIQUE `uq_opportunity_buyer_intel`（`tenant_id`, `opportunity_id`）
  INDEX `ix_opportunity_buyer_intel_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `award_records`

- 模型类：`AwardRecord`（`backend/app/modules/opportunities/models.py`）
- 创建迁移：`0002_p0_opportunities.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：中标记录：与 opportunity 弱关联（SET NULL），tenant+dedup_key 唯一，用于中标分析与采购方情报。
- 列（13）：`id` `tenant_id` `opportunity_id` `dedup_key` `project_code` `project_name` `winner` `win_amount` `award_at` `source` `source_url` `fetched_at` `created_at`
- 约束与索引：
  UNIQUE `uq_award_records_tenant_dedup`（`tenant_id`, `dedup_key`）
  INDEX `ix_award_records_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

## 模块 `org`


### `org_units`

- 模型类：`OrgUnit`（`backend/app/modules/org/models.py`）
- 创建迁移：`0032_p2_org_units.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：组织架构部门/团队：自引用 parent_id + path 物化路径，支撑部门级数据范围与资产归属。
- 列（10）：`id` `tenant_id` `parent_id` `kind` `name` `path` `sort_order` `status` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_org_units_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_org_units_tenant_parent`（`tenant_id`, `parent_id`）
  INDEX `ix_org_units_tenant_path`（`tenant_id`, `path`）

### `org_members`

- 模型类：`OrgMember`（`backend/app/modules/org/models.py`）
- 创建迁移：`0032_p2_org_units.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：部门成员关系：is_primary 标记主部门，member_role 区分 member/manager。
- 列（7）：`id` `tenant_id` `org_unit_id` `user_id` `member_role` `is_primary` `created_at`
- 约束与索引：
  UNIQUE `uq_org_members_unit_user`（`org_unit_id`, `user_id`）
  INDEX `ix_org_members_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_org_members_tenant_user`（`tenant_id`, `user_id`）

## 模块 `projects`


### `bid_projects`

- 模型类：`BidProject`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0004_p1_projects.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：投标项目主表：串联商机、公司、标书解析、生成、合规与合同审查的全流程根实体。
- 列（18）：`id` `tenant_id` `opportunity_id` `company_id` `name` `code` `buyer` `status` `owner_id` `bid_deadline` `package_code` `decision_status` `decision_json` `decision_at` `decision_by` `template_code` `format_code` `created_at`
- 约束与索引：
  INDEX `ix_bid_projects_tenant_created`（`tenant_id`, `created_at`）
  INDEX `ix_bid_projects_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_bid_projects_tenant_status`（`tenant_id`, `status`）

### `tender_documents`

- 模型类：`TenderDocument`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0004_p1_projects.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：招标文件上传记录：文件字节不入库（object_ref 指向对象存储），file_hash 去重，supersedes_id 支持版本替换。
- 列（15）：`id` `tenant_id` `project_id` `filename` `content_type` `size_bytes` `file_hash` `object_ref` `parse_status` `parse_error` `parsed_at` `ocr_used` `version` `supersedes_id` `created_at`
- 约束与索引：
  INDEX `ix_tender_documents_project_version`（`project_id`, `version`）（迁移生成，模型侧为列级 index）
  INDEX `ix_tender_documents_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_tender_documents_tenant_project`（`tenant_id`, `project_id`）

### `tender_constraints`

- 模型类：`TenderConstraint`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0004_p1_projects.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：招标约束集：每个 project+version 一份结构化 payload（资格/实质性/澄清/评分项）与 parse_meta。
- 列（8）：`id` `tenant_id` `project_id` `document_id` `version` `payload` `parse_meta` `created_at`
- 约束与索引：
  UNIQUE `uq_tender_constraints_project_version`（`project_id`, `version`）
  INDEX `ix_tender_constraints_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `scoring_items`

- 模型类：`ScoringItem`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0004_p1_projects.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：评分项：从约束集拆出的打分条目，带 category/score/criteria/source_span/confidence。
- 列（11）：`id` `tenant_id` `project_id` `constraint_id` `category` `item` `score` `criteria` `source_span` `confidence` `created_at`
- 约束与索引：
  INDEX `ix_scoring_items_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_scoring_items_tenant_project`（`tenant_id`, `project_id`）

### `response_matrix`

- 模型类：`ResponseMatrix`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0004_p1_projects.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：评分项响应矩阵：对每个评分项给出响应策略、所需证据、已匹配资产与覆盖状态。
- 列（12）：`id` `tenant_id` `project_id` `scoring_item_id` `response_strategy` `evidence_required` `matched_assets` `missing_evidence` `section_mapping` `coverage_status` `risk_note` `created_at`
- 约束与索引：
  UNIQUE `uq_response_matrix_scoring_item`（`scoring_item_id`）
  INDEX `ix_response_matrix_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_response_matrix_tenant_project`（`tenant_id`, `project_id`）

### `bid_results`

- 模型类：`BidResult`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0008_p1_bid_results.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：投标结果回填：中标/落标金额、原因、竞争对手，供复盘统计。
- 列（11）：`id` `tenant_id` `project_id` `outcome` `win_amount` `loss_reason` `competitor` `notes` `recorded_by` `recorded_at` `created_at`
- 约束与索引：
  UNIQUE `uq_bid_results_project`（`project_id`）
  INDEX `ix_bid_results_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_bid_results_tenant_recorded`（`tenant_id`, `recorded_at`）（迁移生成，模型侧为列级 index）

### `compliance_responses`

- 模型类：`ComplianceResponse`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0017_p2_compliance_responses.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：逐条实质性要求响应/偏离表：对招标文件每项要求给出状态与依据，支持确认人落库。
- 列（16）：`id` `tenant_id` `project_id` `constraint_id` `requirement_type` `requirement_key` `requirement_text` `category` `source_span` `response_status` `response_note` `evidence_refs` `section_ref` `confirmed_by` `confirmed_at` `created_at`
- 约束与索引：
  UNIQUE `uq_compliance_responses_key`（`constraint_id`, `requirement_key`）
  INDEX `ix_compliance_responses_tenant_project`（`tenant_id`, `project_id`）

### `parse_jobs`

- 模型类：`ParseJob`（`backend/app/modules/projects/models.py`）
- 创建迁移：`0026_p3_doc_versions.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：长文档异步解析任务：状态机 + progress，供前端轮询进度。
- 列（9）：`id` `tenant_id` `project_id` `document_id` `status` `progress` `error` `created_by` `created_at`
- 约束与索引：
  INDEX `ix_parse_jobs_tenant_status`（`tenant_id`, `status`）

## 模块 `system`


### `model_configs`

- 模型类：`ModelConfig`（`backend/app/modules/system/models.py`）
- 创建迁移：`0015_p2_model_configs.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：模型接入配置：按 purpose(main/judge) 与 scope(tenant/personal) 选定 provider/base_url/model，密钥外置到 secret_store。
- 列（13）：`id` `tenant_id` `purpose` `scope` `owner_id` `provider` `base_url` `model` `enabled` `secret_ref` `key_last4` `updated_by` `created_at`
- 约束与索引：
  部分唯一索引 `uq_model_configs_personal_purpose`（`tenant_id`, `owner_id`, `purpose`）WHERE scope = 'personal'
  部分唯一索引 `uq_model_configs_tenant_purpose`（`tenant_id`, `purpose`）WHERE scope = 'tenant'
  INDEX `ix_model_configs_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_model_configs_tenant_scope`（`tenant_id`, `scope`）

### `secret_store`

- 模型类：`ModelSecret`（`backend/app/modules/system/models.py`）
- 创建迁移：`0031_p1b_model_secrets.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：模型密钥保管箱：ciphertext+nonce+wrapped_dek 信封加密，key_version 支持轮换，rotated_at/revoked_at 记录生命周期。
- 列（15）：`id` `tenant_id` `scope` `owner_id` `provider` `ciphertext` `nonce` `wrapped_dek` `dek_nonce` `key_version` `last4` `created_by` `rotated_at` `revoked_at` `created_at`
- 约束与索引：
  INDEX `ix_secret_store_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）
  INDEX `ix_secret_store_tenant_owner`（`tenant_id`, `owner_id`）
  INDEX `ix_secret_store_tenant_scope`（`tenant_id`, `scope`）

### `tenant_settings`

- 模型类：`TenantSetting`（`backend/app/modules/system/models.py`）
- 创建迁移：`0035_p2c_model_policy.py`
- 租户隔离：**是** — TenantMixin（tenant_id NOT NULL + FK CASCADE + 索引）
- 用途：租户级键值设置：key + value(JSONB) 存放租户自定义参数。
- 列（6）：`id` `tenant_id` `key` `value`(JSONB) `updated_by` `created_at`
- 约束与索引：
  UNIQUE `uq_tenant_settings_tenant_key`（`tenant_id`, `key`）
  INDEX `ix_tenant_settings_tenant_id`（`tenant_id`）（迁移生成，模型侧为列级 index）

### `resource_acl`

- `subject_type`
- `role`
- `resource_type`

### `companies`

- `source`

### `company_qualifications`

- `source`

### `company_personnel`

- `source`

### `company_performances`

- `source`

### `company_credits`

- `source`
- `status`

### `company_bids`

- `role`
- `info_type`
- `source`

### `field_policies`

- `min_clearance`
- `mask_strategy`
- `resource_type`

### `compliance_reports`

- `status`

### `findings`

- `category`
- `severity`
- `finding_type`
- `status`

### `policies`

- `severity_override`

### `compliance_access_requests`

- `resource_type`
- `status`

### `contract_reviews`

- `status`

### `contract_clauses`

- `category`

### `contract_findings`

- `kind`
- `severity`
- `source`
- `status`

### `contract_milestones`

- `kind`
- `status`

### `cost_ledger`

- `pcode`
- `operation`
- `status`

### `bid_evaluations`

- `status`

### `generation_runs`

- `mode`
- `status`

### `bid_sections`

- `section_type`
- `status`
- `generation_mode`
- `factcheck_status`

### `evidence_links`

- `claim_type`
- `status`
- `source_type`

### `tenants`

- `kind`
- `status`

### `users`

- `status`
- `clearance`

### `roles`

- `scope_types`

### `user_roles`

- `scope_type`

### `knowledge_bases`

- `scope`
- `classification`
- `visibility`
- `status`

### `knowledge_packs`

- `category`
- `scope`
- `classification`
- `status`

### `knowledge_documents`

- `kind`
- `classification`
- `status`

### `notifications`

- `type`
- `resource_type`

### `source_sync_runs`

- `source`
- `status`
- `operation`

### `opportunities`

- `source`
- `info_type`
- `detail_status`
- `confidence`

### `opportunity_buyer_intel`

- `status`

### `org_units`

- `kind`
- `status`

### `org_members`

- `member_role`

### `bid_projects`

- `status`
- `decision_status`

### `tender_documents`

- `parse_status`

### `scoring_items`

- `category`
- `confidence`

### `response_matrix`

- `coverage_status`

### `bid_results`

- `outcome`
- `loss_reason`

### `compliance_responses`

- `requirement_type`
- `response_status`
- `category`

### `parse_jobs`

- `status`

### `model_configs`

- `purpose`
- `scope`
- `provider`

### `secret_store`

- `scope`
- `provider`
- 列（15）：`id` `tenant_id` `scope` `owner_id` `provider` `ciphertext`(LargeBinary) `nonce`(LargeBinary) `wrapped_dek`(LargeBinary) `dek_nonce`(LargeBinary) `key_version` `last4` `created_by` `created_at` `rotated_at` `revoked_at`

---

口径：沿迁移链 0001→0038 正向推演后的**最终生效状态**（含 upgrade 中的 create/drop，不含 downgrade）。
迁移过程中被替换的约束：`user_roles.uq_user_roles_user_role`（0038 删除，由 `uq_user_roles_scoped` 取代）、`model_configs.uq_model_configs_tenant_purpose`（0031 由普通唯一约束改为**部分唯一索引**）。

### A.1 普通唯一约束

| 表 | 约束名 | 列 |
| --- | --- | --- |
| `resource_acl` | `uq_resource_acl_subject` | `tenant_id`, `resource_type`, `resource_id`, `subject_type`, `subject_id` |
| `companies` | `uq_companies_tenant_name` | `tenant_id`, `name` |
| `company_qualifications` | `uq_qualifications_tenant_dedup` | `tenant_id`, `dedup_key` |
| `company_personnel` | `uq_personnel_tenant_dedup` | `tenant_id`, `dedup_key` |
| `company_performances` | `uq_performances_tenant_dedup` | `tenant_id`, `dedup_key` |
| `company_credits` | `uq_credits_tenant_dedup` | `tenant_id`, `dedup_key` |
| `company_bids` | `uq_company_bids_tenant_dedup` | `tenant_id`, `dedup_key` |
| `field_policies` | `uq_field_policies_field` | `tenant_id`, `resource_type`, `field_name` |
| `policies` | `uq_policies_tenant_rule` | `tenant_id`, `rule_id` |
| `contract_reviews` | `uq_contract_reviews_hash` | `tenant_id`, `project_id`, `file_hash` |
| `tenant_contract_rules` | `uq_tenant_contract_rules_name` | `tenant_id`, `name` |
| `bid_sections` | `uq_bid_sections_project_order` | `project_id`, `order_index` |
| `roles` | `uq_roles_tenant_code` | `tenant_id`, `code` |
| `user_roles` | `uq_user_roles_scoped` | `user_id`, `role_id`, `scope_type`, `scope_id` |
| `knowledge_packs` | `uq_knowledge_packs_code` | `code` |
| `opportunities` | `uq_opportunities_tenant_source_dedup` | `tenant_id`, `source`, `dedup_key` |
| `opportunity_favorites` | `uq_opportunity_favorites` | `tenant_id`, `user_id`, `opportunity_id` |
| `opportunity_subscriptions` | `uq_opportunity_subscriptions_name` | `tenant_id`, `user_id`, `name` |
| `opportunity_buyer_intel` | `uq_opportunity_buyer_intel` | `tenant_id`, `opportunity_id` |
| `award_records` | `uq_award_records_tenant_dedup` | `tenant_id`, `dedup_key` |
| `org_members` | `uq_org_members_unit_user` | `org_unit_id`, `user_id` |
| `tender_constraints` | `uq_tender_constraints_project_version` | `project_id`, `version` |
| `response_matrix` | `uq_response_matrix_scoring_item` | `scoring_item_id` |
| `bid_results` | `uq_bid_results_project` | `project_id` |
| `compliance_responses` | `uq_compliance_responses_key` | `constraint_id`, `requirement_key` |
| `tenant_settings` | `uq_tenant_settings_tenant_key` | `tenant_id`, `key` |

共 **26** 个命名唯一约束。此外以下列为列级 `unique=True`：`tenants.slug`、`users.email`、`audit_events.seq`。

### A.2 部分唯一索引（partial unique index）

这些约束在迁移中通过 `op.create_index(..., unique=True, postgresql_where=...)` 创建，语义是唯一约束但仅对满足 WHERE 的行生效：

| 表 | 索引名 | 列 | WHERE |
| --- | --- | --- | --- |
| `roles` | `uq_roles_system_code` | `code` | `tenant_id IS NULL` |
| `model_configs` | `uq_model_configs_personal_purpose` | `tenant_id`, `owner_id`, `purpose` | `scope = 'personal'` |
| `model_configs` | `uq_model_configs_tenant_purpose` | `tenant_id`, `purpose` | `scope = 'tenant'` |
| `cost_ledger` | `uq_cost_ledger_tenant_order` | `tenant_id`, `order_no` | `order_no IS NOT NULL` |

共 **4** 个部分唯一索引。其中 `cost_ledger.uq_cost_ledger_tenant_order`（迁移 `0073_cost_ledger_order.py`）有**升级前置预检**：建索引前先按 `(tenant_id, order_no)` 分组检查重复账目，存在 `COUNT(*) > 1` 即抛 `RuntimeError` 并列出前 10 组 `tenant_id/order_no/count/first_created_at`，须人工核对网关回执、清理重复账目后重跑迁移；迁移不自动删除或修改财务数据。

---

以下列均为 `String` 存储、代码中存在受控词表、Literal 白名单或常量集合的字段。观测值附代码位置；无完整词表的标注「待确认」。

### `resource_acl`

- `subject_type`
  - user / tenant / org_unit — `app/modules/knowledge/access.py:344` 白名单
- `role`
  - viewer / user / editor / manager — `ROLE_RANK` in `app/modules/knowledge/access.py:18`
- `resource_type`
  - bid_project, bid_evaluation, company, contract_review, contract_finding, contract_milestone, tenant_contract_rule, knowledge_base, knowledge_document, knowledge_image, knowledge_pack, model_config, opportunity, opportunity_subscription, org_unit, parse_job, personal_resource, project, source_sync_run, tenant, user, web_search

### `companies`

- `source`
  - manual（默认）/ import — `app/modules/assets/importer.py:119` / demo — `app/demo.py:114`

### `company_qualifications`

- `source`
  - manual（默认）/ import / demo — 同上赋值点

### `company_personnel`

- `source`
  - manual（默认）/ import / demo

### `company_performances`

- `source`
  - manual（默认）/ import / demo

### `company_credits`

- `source`
  - manual（默认）/ import / demo
- `status`
  - 自由文本（String(80)），`app/demo.py:200` 观测到「未发现」；`matching.py` 以「正常/异常/失信/违法/注销」关键词判定，无受控词表

### `company_bids`

- `role`
  - tenderer / winner / agency — `ROLE_SEARCH_TYPES` in `app/modules/gateway/company_adapter.py:28`
- `info_type`
  - tender / planned / award / other（默认）— 同 opportunities.info_type 口径
- `source`
  - keeprisk:E_007_1271 等（String(80)，非受控词表）

### `field_policies`

- `min_clearance`
  - public / internal / confidential（默认）/ restricted — `app/modules/assets/schemas.py:123` Literal
- `mask_strategy`
  - hide / mask（默认）/ partial — `app/modules/assets/schemas.py:124` Literal
- `resource_type`
  - 自由文本（String(60)），观测值同 resource_acl.resource_type

### `compliance_reports`

- `status`
  - completed — 默认 / `app/modules/compliance/service.py:169`
  - 其他值待确认

### `findings`

- `category`
  - completeness / consistency / dark_bid / format / price / pricing / qualification / responsiveness / time / content / commercial — `backend/app/data/rules/compliance_rules.json`（version 2026-10-25.4，29 条规则观测）
- `severity`
  - high / medium / low — 同上 JSON 观测
- `finding_type`
  - HARD / SOFT — `app/modules/compliance/registry.py:32` 校验
- `status`
  - open — 默认
  - 其他值待确认：`router.py:173` 接受任意 body.status，无白名单

### `policies`

- `severity_override`
  - high / medium / low — 继承 findings.severity 口径，具体赋值点待确认

### `compliance_access_requests`

- `resource_type`
  - knowledge_base / knowledge_pack — `RESOURCE_TYPES` in `app/modules/compliance/access_service.py:13`
  - personal_resource — `app/modules/compliance/personal_resources_router.py:100`
- `status`
  - pending — 默认
  - 其他值待确认

### `contract_reviews`

- `status`
  - reviewed — 默认 / `app/modules/contracts/router.py:424`
  - 其他值待确认

### `contract_clauses`

- `category`
  - payment / penalty / price / schedule / quality / acceptance / ip / confidentiality / dispute / other（默认）— `CLAUSE_CATEGORY_KEYWORDS` + `CATEGORY_LABELS` in `app/modules/contracts/service.py:42-62`

### `contract_findings`

- `kind`
  - risk — 默认兜底 / `service.py:333,715`
  - missing — `service.py:415,436,752`
  - deviation — `service.py:386,765`
- `severity`
  - high / medium（默认）/ low — `app/modules/contracts/service.py:572` 白名单
- `source`
  - rule（默认）/ llm — `app/modules/contracts/service.py:514,572`
- `status`
  - pending — 默认
  - confirmed / dismissed — `app/modules/contracts/router.py:213-214` 白名单

### `contract_milestones`

- `kind`
  - payment / delivery / warranty / acceptance — `MILESTONE_KIND_KEYWORDS` in `app/modules/contracts/service.py:653-658`
- `status`
  - upcoming — 默认
  - due — `app/modules/contracts/service.py:817` 到期置位

### `cost_ledger`

- `pcode`
  - E_007_1271 / E_007_1341 / llm / web_search:{provider} — 各调用方字面量（`opportunities/router.py`、`company_intel/router.py`、`contracts/router.py`、`web_search/router.py`），非受控词表
- `operation`
  - opportunity.list / opportunity.detail / opportunity.buyer_intel / company.profile / company.bids.{role} / contract.deep_review / tender.llm_fallback / web.search
- `status`
  - DataResult.status 原样落库：ok / empty / unavailable / rate_limited / unauthorized / contract_error — `SourceStatus` in `app/modules/gateway/contract.py:6-12`；`costs/service.py:56` 以 `{ok, empty, succeeded}` 为成功集

### `bid_evaluations`

- `status`
  - running — 默认 / `app/modules/evaluation/service.py:331`
  - completed — `service.py:368`
  - failed — `service.py:382`

### `generation_runs`

- `mode`
  - rules / llm — `app/modules/generation/service.py:703,753`
- `status`
  - running — 默认 / `service.py:716`
  - succeeded — `service.py:787`
  - failed — `service.py:794,801`

### `bid_sections`

- `section_type`
  - cover / qualification / scoring / commitment — `app/modules/generation/service.py:631,638,650,662`
  - other — `service.py:619` 兜底
- `status`
  - draft — 默认 / `service.py:777,879`
  - approved — `service.py:941`
- `generation_mode`
  - rules — `app/demo.py:411`
  - llm 待确认
- `factcheck_status`
  - pending — 默认
  - 其他值待确认

### `evidence_links`

- `claim_type`
  - amount / date / percent / duration / score / standard — `CLAIM_PATTERNS` in `app/modules/generation/factcheck.py:4-11`
  - other — 模型默认
- `status`
  - unsourced（默认）/ sourced — `app/modules/generation/service.py:519,533`
- `source_type`
  - project / knowledge / tender / dark_bid — `app/modules/generation/service.py:91,116,126,135,175`
  - 资产类 kind：qualifications / personnel / performances / credits — `service.py:143-160` _asset_corpus

### `tenants`

- `kind`
  - platform — 企业版 CLI 创建平台租户（社区版无此引导）
  - customer — 模型默认值 / `app/demo.py:73`
- `status`
  - active — `app/cli.py:20`
  - 其他值待确认（无代码赋值点）

### `users`

- `status`
  - active — 模型默认 / `app/cli.py:48`
  - disabled — `app/modules/identity/schemas.py:79` Literal
- `clearance`
  - public / internal（默认）/ confidential / restricted — `CLEARANCE_RANK` in `app/modules/knowledge/access.py:17`

### `roles`

- `scope_types`
  - ['tenant'] — `app/modules/identity/users_service.py:36` 默认
  - 其他值待确认

### `user_roles`

- `scope_type`
  - tenant — 模型默认 / `app/cli.py:68`
  - 其他值待确认

### `knowledge_bases`

- `scope`
  - tenant（默认）/ personal / org_unit — `app/modules/knowledge/schemas.py:81` Literal
  - system — `app/modules/knowledge/builtin.py:241` 内置包
- `classification`
  - public / internal（默认）/ confidential / restricted — `app/modules/knowledge/schemas.py:84` Literal
- `visibility`
  - enterprise（默认）/ shared — `app/modules/knowledge/schemas.py:82` Literal
  - team / private — `app/modules/knowledge/router.py:149,153`，与 schema Literal 不一致，待确认
- `status`
  - active — `app/modules/knowledge/access.py:112`
  - 其他值待确认
- `owner_status`
  - active — 默认 / `access.py:112`
  - orphaned — `app/modules/compliance/personal_resources_service.py`

### `knowledge_packs`

- `category`
  - structure / format — `app/modules/knowledge/builtin.py:232,259` 内置包
  - 其他值待确认
- `scope`
  - tenant（默认）/ personal / system — `builtin.py:241`
- `classification`
  - public / internal（默认）/ confidential / restricted
- `status`
  - published — 默认 / `builtin.py:232`
  - 其他值待确认
- `owner_status`
  - active — 默认

### `knowledge_documents`

- `kind`
  - 自由文本（≤30 字符），默认 other — `app/modules/knowledge/kb.py:305` `kind[:30] or 'other'`；无受控词表
- `classification`
  - public / internal（默认）/ confidential / restricted
- `status`
  - parsed — 默认 / `app/modules/knowledge/kb.py:299`
  - 其他值待确认

### `notifications`

- `type`
  - sync_new_rows / sync_failed — `app/modules/opportunities/subscriptions.py:317`
  - contract_milestone_due — `app/modules/contracts/service.py:810`
- `resource_type`
  - opportunity_subscription / source_sync_run / contract_milestone — `subscriptions.py`、`contracts/service.py:813`

### `source_sync_runs`

- `source`
  - keeprisk:E_007_1271 — `app/modules/opportunities/router.py:321` 等
- `status`
  - queued — 默认
  - running — `sync.py:29`
  - succeeded — `sync.py:151`
  - failed — `sync.py:46` / `subscriptions.py:224,253`
- `operation`
  - opportunity.list / opportunity.detail / opportunity.buyer_intel — `app/modules/opportunities/router.py:342,176,424`

### `opportunities`

- `source`
  - keeprisk:E_007_1271 — `app/modules/gateway/normalize.py:219,227`
- `info_type`
  - tender / planned / award / other（默认）— `_INFO_TYPES` in `app/modules/gateway/normalize.py:11`
- `detail_status`
  - list_only（默认）/ full — `app/modules/gateway/normalize.py`
- `confidence`
  - high（默认）/ low / uie — `app/modules/gateway/normalize.py`、`app/config.py`
  - medium 是否使用待确认

### `opportunity_buyer_intel`

- `status`
  - queued — 默认 / `app/modules/opportunities/router.py:326`
  - DataResult.status 原样落库：ok / empty / unavailable / rate_limited / unauthorized / contract_error — `buyer_intel.py:144-145` 注释明确说明不再二值化

### `org_units`

- `kind`
  - department（默认）/ team — `app/modules/org/schemas.py:9` Literal
- `status`
  - active — `app/modules/org/service.py:31`

### `org_members`

- `member_role`
  - member（默认）/ manager — `app/modules/org/schemas.py:27` Literal

### `bid_projects`

- `status`
  - active — `app/modules/projects/service.py:86` 唯一赋值点
  - 其他值待确认（matching.py 的 Go/No-Go 属 decision_status，非本列）
- `decision_status`
  - Go / Conditional Go / No-Go — `app/modules/projects/matching.py:244-249` build_decision

### `tender_documents`

- `parse_status`
  - queued / parsing — `app/modules/projects/service.py:437` 二选一赋初值
  - parsed — `service.py:350` 成功
  - failed — `service.py:330-345,1000`
  - pending — 模型默认值，无代码显式赋值

### `scoring_items`

- `category`
  - 自由文本，默认「其他」— `service.py:374` `item.get('category') or '其他'`；matching.py 的 QUALIFICATION/PERSONNEL/PERFORMANCE/CREDIT 是资产匹配类别字典键，非本列
- `confidence`
  - high / medium（默认）/ low — `CONFIDENCE_VALUES` in `app/modules/evaluation/service.py:28`

### `response_matrix`

- `coverage_status`
  - covered / partial / missing — `app/modules/projects/matching.py:240-242`

### `bid_results`

- `outcome`
  - won / lost — `app/demo.py:449`
  - 其他值待确认
- `loss_reason`
  - price — `app/demo.py`
  - 完整词表待确认（String(40)，其余为自由文本）

### `compliance_responses`

- `requirement_type`
  - qualification / substantive / clarification / scoring — `app/modules/projects/service.py:704,715,721,728` _response_items
- `response_status`
  - pending — 默认 / `service.py:761`
  - responded / deviated / not_responded — `RESPONSE_STATUSES` in `service.py:688`
- `category`
  - 自由文本，默认「其他」

### `parse_jobs`

- `status`
  - queued — 默认 / `service.py:453`
  - running / succeeded / failed — 模型 docstring 声明的状态机，赋值点待确认

### `model_configs`

- `purpose`
  - main / judge — `PURPOSES` in `app/modules/system/service.py:13`
- `scope`
  - tenant（默认）/ personal — `app/modules/system/router.py:156,209`
- `provider`
  - openai_compatible — 模型默认 / `app/modules/system/service.py:247,252`（唯一观测值）

### `secret_store`

- `scope`
  - tenant / personal — `app/modules/system/secrets.py:50`
- `provider`
  - openai_compatible（唯一观测值）

---

| 迁移 | 创建的表 |
| --- | --- |
| `0001_p0_identity.py` | `audit_events`, `roles`, `tenants`, `user_roles`, `users` |
| `0002_p0_opportunities.py` | `award_records`, `opportunities`, `opportunity_favorites`, `opportunity_snapshots`, `source_sync_runs` |
| `0003_p0_assets.py` | `companies`, `company_credits`, `company_performances`, `company_personnel`, `company_qualifications` |
| `0004_p1_projects.py` | `bid_projects`, `response_matrix`, `scoring_items`, `tender_constraints`, `tender_documents` |
| `0005_p2_generation.py` | `bid_sections`, `evidence_links`, `generation_runs` |
| `0006_p3_compliance.py` | `compliance_reports`, `findings` |
| `0007_p1_company_bids.py` | `company_bids` |
| `0008_p1_bid_results.py` | `bid_results` |
| `0009_p1_ocr_provenance.py` | （仅改列/索引，无新建表） |
| `0010_p3_bid_evaluations.py` | `bid_evaluations` |
| `0011_p1_cost_ledger.py` | `cost_ledger` |
| `0012_p0_token_version.py` | （仅改列/索引，无新建表） |
| `0013_p1_templates.py` | `knowledge_packs` |
| `0014_p3_policies.py` | `policies` |
| `0015_p2_model_configs.py` | `model_configs` |
| `0016_p2_knowledge_base.py` | `knowledge_chunks`, `knowledge_documents`, `knowledge_images` |
| `0017_p2_compliance_responses.py` | `compliance_responses` |
| `0018_p2_project_package.py` | （仅改列/索引，无新建表） |
| `0019_p2_subs_notifications.py` | `notifications`, `opportunity_subscriptions` |
| `0020_p2_buyer_intel.py` | `opportunity_buyer_intel` |
| `0021_p2_intel_synced_at.py` | （仅改列/索引，无新建表） |
| `0022_p2_fallback_dedup.py` | （仅改列/索引，无新建表） |
| `0023_p2_sync_warnings.py` | （仅改列/索引，无新建表） |
| `0024_p3_contracts.py` | `contract_clauses`, `contract_findings`, `contract_reviews` |
| `0025_p3_contract_ops.py` | `contract_milestones`, `tenant_contract_rules` |
| `0026_p3_doc_versions.py` | `parse_jobs` |
| `0027_p2_policy_threshold.py` | （仅改列/索引，无新建表） |
| `0028_p0_capability_permissions.py` | （仅改列/索引，无新建表） |
| `0029_p1_knowledge_acl.py` | `knowledge_bases`, `resource_acl` |
| `0030_p1b_template_scope.py` | （仅改列/索引，无新建表） |
| `0031_p1b_model_secrets.py` | `secret_store` |
| `0032_p2_org_units.py` | `org_members`, `org_units` |
| `0033_p2b_break_glass.py` | `compliance_access_requests` |
| `0034_p2c_department_assets.py` | （仅改列/索引，无新建表） |
| `0035_p2c_model_policy.py` | `tenant_settings` |
| `0036_p3b_field_policies.py` | `field_policies` |
| `0037_p3c_custom_roles.py` | （仅改列/索引，无新建表） |
| `0038_p3e_scoped_roles.py` | （仅改列/索引，无新建表） |

---

## 使用说明

- **升级/迁移**：新增表或改列须新增迁移文件并在 `alembic/env.py:11-18` 登记模型模块（autogenerate 发现表的唯一途径）；迁移命名规范见[详细设计文档](2026-10-24-bidcopilot-detailed-design.md) §3.3。
- **新增受控词表**：状态/类型列若使用 String 存储枚举值，请在本文档附录 D 登记取值与出处，避免下游误读。
- **部分唯一索引**：改 `roles`/`model_configs` 的唯一约束时注意 `postgresql_where` 条件（见通用约定）。
- **迁移 0073 升级前置**：`0073_cost_ledger_order.py` 为 `cost_ledger` 创建 `(tenant_id, order_no)` 部分唯一索引前会预检重复账目；存量库若存在重复 `order_no` 组，迁移抛出 `RuntimeError` 并列出重复明细，须人工清理后重跑，财务账本禁止自动清理（详见附录 A.2）。
- 完整逐列类型清单（类型/可空/默认值/FK）以 `backend/app/modules/*/models.py` 与迁移文件为准；本文档为结构维护视角的精炼版。
