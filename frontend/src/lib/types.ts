export type User = {
  id: string;
  tenant_id: string;
  email: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  capabilities?: string[];
  clearance?: string;
  tenant_name: string | null;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: User;
};

export type OpportunityListItem = {
  id: string;
  title: string;
  project_code: string | null;
  project_name: string | null;
  buyer: string | null;
  agency: string | null;
  winner: string | null;
  region: string | null;
  industry: string[];
  budget_amount: string | number | null;
  win_bid_amount: string | number | null;
  publish_at: string | null;
  bid_deadline: string | null;
  info_type: string;
  source: string;
  source_url: string | null;
  detail_status: string;
  fetched_at: string;
  match_hits?: number | null;
  explanation?: {
    reasons: string[];
    risks: string[];
    buyer_watched: boolean;
    buyer_intel: { recent_wins_12m: number; existing_suppliers: string[] } | null;
  } | null;
};

export type Award = {
  winner: string;
  win_amount: string | number | null;
  award_at: string | null;
  source_url: string | null;
};

export type OpportunityDetail = OpportunityListItem & {
  content_html: string | null;
  content_text: string | null;
  content_fetched_at: string | null;
  awards: Award[];
  is_favorite: boolean;
  detail_error: { code: string; message: string } | null;
};

export type ListResponse<T> = {
  items: T[];
  total: number;
  page: number;
  page_size: number;
};

export type SyncWarning = {
  code: string;
  message: string;
  field?: string | null;
};

export type SyncRun = {
  id: string;
  status: string;
  rows_fetched: number;
  rows_upserted: number;
  rows_new: number;
  warnings_count: number;
  warnings: SyncWarning[];
  error_code: string | null;
  error_message: string | null;
  snapshot_ref: string | null;
  gateway_order_no: string | null;
  charged: boolean;
  created_at: string;
  finished_at: string | null;
};

export type SubscriptionQuery = {
  keywords?: string[];
  info_types?: string[];
  publish_from?: string | null;
  publish_to?: string | null;
  industry_list?: string[];
  budget_min?: string | number | null;
  budget_max?: string | number | null;
  page_size?: number;
};

export type OpportunitySubscription = {
  id: string;
  name: string;
  query_json: SubscriptionQuery;
  interval_minutes: number;
  enabled: boolean;
  last_run_at: string | null;
  last_sync_run_id: string | null;
  created_at: string;
};

export type NotificationItem = {
  id: string;
  type: string;
  title: string;
  body: string | null;
  resource_type: string | null;
  resource_id: string | null;
  created_at: string;
  read_at: string | null;
};

export type NotificationListResponse = {
  items: NotificationItem[];
  unread: number;
};

export type BuyerWinner = {
  winner: string;
  wins: number;
  recent_wins_12m?: number;
  latest_at: string | null;
  total_amount: string | null;
  tracked: boolean;
  competitor_noted: boolean;
};

export type OpportunityBuyerIntel = {
  id: string;
  opportunity_id: string;
  buyer_name: string;
  status: string;
  rows_fetched: number;
  recent_wins_12m: number;
  top_winners: BuyerWinner[];
  existing_suppliers: BuyerWinner[];
  winner_rows: Array<Record<string, unknown>>;
  synced_at: string | null;
  snapshot_ref: string | null;
  gateway_order_no: string | null;
  charged: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
};

export type Company = {
  id: string;
  name: string;
  credit_code: string | null;
  region: string | null;
  industry: string | null;
  contacts: Record<string, unknown>;
  source: string;
  employee_count?: number | null;
  annual_revenue?: string | number | null;
  total_assets?: string | number | null;
  enterprise_industry?: string | null;
  enterprise_scale?: string | null;
  enterprise_scale_source?: string;
};

/** 企业划型结果：带依据与缺失项，用户可以核对而不是盲信一个「小型」。 */
/** 划型变化对在投项目的影响。只有跨越中小企业分界线才 needs_review。 */
export type ScaleImpact = {
  previous_scale: string | null;
  current_scale: string | null;
  previous_is_sme: boolean | null;
  current_is_sme: boolean | null;
  crossed_boundary: boolean;
  needs_review: boolean;
  affected_projects: { project_id: string; name: string; bid_deadline: string | null }[];
  message: string;
};

/** 划型变更历史（取自审计事件，带哈希链，可用于声明函纠纷举证）。 */
export type ScaleHistoryEntry = {
  at: string;
  actor_id: string | null;
  from: string | null;
  to: string | null;
  crossed_boundary: boolean;
};

export type CompanyScale = {
  scale: string | null;
  industry: string | null;
  industry_label: string | null;
  basis: string;
  missing: string[];
  is_sme: boolean | null;
  source: string;
  confirmed: boolean;
};

export type CompanyProfileEntry = {
  subject_name: string;
  subject_status: string | null;
  subject_type: string | null;
  region: string | null;
  roles: string[];
  related_count: number;
  bidder_count: number;
  winner_count: number;
  agent_count: number;
  tender_count: number;
  latest_publish_at: string | null;
  latest_title: string | null;
  latest_role: string | null;
  info_type: string | null;
};

export type CompanyProfileResponse = {
  status: string;
  company_id: string;
  company_name: string;
  source: string;
  fetched_at: string;
  order_no: string | null;
  charged: boolean;
  total: number;
  entries: CompanyProfileEntry[];
  warnings: { code: string; message: string }[];
  error: { code: string; message: string } | null;
};

export type CompanyBid = {
  id: string;
  company_id: string;
  role: string;
  title: string;
  project_name: string | null;
  project_code: string | null;
  buyer: string | null;
  agency: string | null;
  winner: string | null;
  budget_amount: string | number | null;
  win_bid_amount: string | number | null;
  publish_at: string | null;
  info_type: string;
  source_url: string | null;
  source: string;
  fetched_at: string;
};

export type CompanyBidSyncResponse = {
  company_id: string;
  company_name: string;
  upserted: number;
  results: {
    role: string;
    status: string;
    fetched: number;
    upserted: number;
    order_no: string | null;
    charged: boolean;
    warnings: { code: string; message: string }[];
    error: { code: string; message: string } | null;
  }[];
};

export type AssetItem = {
  id: string;
  company_id: string;
  org_unit_id: string | null;
  kind: string;
  name: string | null;
  level: string | null;
  cert_no: string | null;
  issuer: string | null;
  valid_from: string | null;
  valid_to: string | null;
  cert_type: string | null;
  major: string | null;
  project_name: string | null;
  amount: string | number | null;
  complete_date: string | null;
  buyer: string | null;
  category: string | null;
  check_item: string | null;
  status: string | null;
  checked_at: string | null;
  source: string;
};

export type ImportResult = {
  imported: number;
  failed: number;
  errors: { row: number; field: string | null; message: string }[];
};

export type ExpiringItem = {
  id: string;
  company_id: string;
  kind: string;
  name: string;
  valid_to: string;
};

export type Project = {
  id: string;
  opportunity_id: string | null;
  company_id: string | null;
  name: string;
  code: string | null;
  buyer: string | null;
  status: string;
  bid_deadline: string | null;
  internal_deadline?: string | null;
  decision_status: string | null;
  decision_json: Record<string, unknown>;
  template_code?: string | null;
  format_code?: string | null;
  created_at: string;
};

export type BidResult = {
  id: string;
  project_id: string;
  outcome: "won" | "lost" | "cancelled";
  win_amount: string | number | null;
  loss_reason: string | null;
  competitor: string | null;
  notes: string | null;
  recorded_by: string | null;
  recorded_at: string;
};

export type BidAnalytics = {
  summary: {
    total: number;
    won: number;
    lost: number;
    cancelled: number;
    win_rate: number;
    win_amount: string | number;
  };
  by_loss_reason: { reason: string; label: string; count: number }[];
  by_month: {
    month: string;
    total: number;
    won: number;
    lost: number;
    cancelled: number;
    win_rate: number;
  }[];
};

export type EvaluationItem = {
  scoring_item_id: string;
  category: string;
  item: string;
  max_score: number;
  /** null 表示模型未返回该项、尚未评分，与"评了得 0 分"含义不同。 */
  score: number | null;
  rationale: string;
  evidence: string[];
  gaps: string[];
  confidence: string;
  /** 合成置信度 0~1（JSON 修复次数 + 结构完整性 + 模型自评）；未评估项为 null。 */
  confidence_score?: number | null;
  review_level?: "auto" | "suggested" | "required" | null;
  review_reason?: string | null;
  /** 模型自评原值，用于对比合成值。 */
  model_confidence?: string | null;
  /** 响应矩阵记录缺证据、评分却很高：需人工核对的矛盾。 */
  matrix_conflict?: string | null;
  needs_review: boolean;
  evaluated?: boolean;
};

export type EvaluationSummary = {
  total_score: number;
  max_score: number;
  percentage: number;
  band: string;
  overall_summary: string;
  risks: string[];
  improvements: string[];
  needs_review_count: number;
  /** provisional 表示有评分项未评估，band 只代表已评部分。 */
  band_status?: "final" | "provisional";
  coverage?: {
    evaluated_items: number;
    expected_items: number;
    ratio: number;
  };
  incomplete_items?: {
    scoring_item_id: string;
    category: string;
    item: string;
    max_score: number;
    reason: string;
  }[];
  unevaluated_count?: number;
  expected_max_score?: number;
  confidence_summary?: {
    auto: number;
    suggested: number;
    required: number;
  };
  min_confidence?: number;
  json_repair_count?: number;
  matrix_conflict_count?: number;
  matrix_conflicts?: {
    scoring_item_id: string;
    item: string;
    reason: string;
  }[];
};

export type Evaluation = {
  id: string;
  project_id: string;
  status: string;
  model: string | null;
  prompt_version: string;
  result_json: {
    summary?: EvaluationSummary;
    items?: EvaluationItem[];
    warnings?: string[];
    meta?: Record<string, unknown>;
  };
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  finished_at: string | null;
};

export type DataOps = {
  period_days: number;  costs: {
    total_calls: number;
    charged_calls: number;
    failed_calls: number;
    rows: number;
    by_pcode: {
      pcode: string;
      calls: number;
      charged_calls: number;
      failed_calls: number;
      rows: number;
    }[];
    by_day: { date: string; calls: number; charged_calls: number }[];
    recent: {
      pcode: string;
      operation: string;
      status: string;
      order_no: string | null;
      charged: boolean;
      rows: number;
      created_at: string;
    }[];
  };
  sources: {
    source: string;
    pcode: string;
    runs: number;
    succeeded: number;
    failed: number;
    success_rate: number;
    last_status: string | null;
    last_error: string | null;
    last_failed_error: string | null;
    last_failed_at: string | null;
    last_run_at: string | null;
  }[];
};

export type TenderDocument = {
  id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  file_hash: string;
  object_ref: string;
  parse_status: string;
  parse_error: string | null;
  parsed_at: string | null;
  ocr_used: boolean;
  document_kind: string;
  assembly_meta: {
    analyzed?: boolean;
    format?: string;
    has_cover_name?: boolean | null;
    has_toc?: boolean | null;
    has_page_numbers?: boolean | null;
    page_count?: number | null;
  };
};

export type ConstraintPayload = {
  project: Record<string, unknown>;
  reject_clauses: Array<Record<string, any>>;
  scoring_items: Array<Record<string, any>>;
  qualification_requirements: Array<Record<string, any>>;
  format_rules: Array<Record<string, any>>;
  timeline: Array<Record<string, any>>;
  budget: { amount: string | number | null; currency: string; source_span?: unknown };
  submission_checklist: Array<Record<string, any>>;
  dark_bid: { required: boolean; rules: Array<Record<string, any>> };
  bid_risks?: BidRisks;
  parse_meta: Record<string, unknown>;
};

export type BidRiskItem = {
  code: string;
  name: string;
  level: "blocker" | "high" | "medium";
  hint: string;
  quote: string;
  years?: number | null;
  min_count?: number | null;
  min_amount?: number | null;
  source_span?: Record<string, any>;
};

export type BidMustBring = {
  code: string;
  item: string;
  why: string;
  critical: boolean;
  source?: string;
};

export type BidRisks = {
  items: BidRiskItem[];
  must_bring?: BidMustBring[];
  flags: {
    consortium_allowed: boolean;
    branch_allowed: boolean;
    sme_only: boolean;
    original_required: boolean;
    credit_query_required: boolean;
  };
  summary: {
    total: number;
    raw_hits?: number;
    blocker: number;
    high: number;
    medium: number;
    verdict: string;
  };
};

export type TenderConstraint = {
  id: string;
  version: number;
  payload: ConstraintPayload;
  parse_meta: Record<string, unknown>;
  created_at: string;
};

export type ProjectDetail = {
  project: Project;
  latest_constraint: TenderConstraint | null;
  documents: TenderDocument[];
  scoring_item_count: number;
};

export type MatrixRow = {
  scoring_item_id: string;
  item: string;
  category: string;
  score: string | number | null;
  coverage_status: string;
  evidence_required: string[];
  matched_assets: { kind: string; id: string; name: string }[];
  missing_evidence: string[];
  response_strategy: string;
  risk_note: string | null;
  section_mapping: string | null;
};

export type QualificationItem = {
  requirement_index: number;
  category: string;
  text: string;
  level: string | null;
  quantity: number | null;
  status: string;
  reason: string;
  matched_asset_ids: string[];
  source_span?: Record<string, unknown> | null;
};

export type Decision = {
  status: string;
  summary: string;
  factors: { name: string; value: string }[];
};

export type DecisionDimension = {
  key: string;
  label: string;
  score: number | null;
  weight: number;
  scored: boolean;
  reason: string;
  source: string;
};

export type DecisionVeto = {
  code: string;
  label: string;
  reason: string;
};

// 决策证据链（P1-8）：每项都带取数口径与「样本不足」显式标记。
export type DecisionEvidenceItem = {
  key: string;
  label: string;
  scored: boolean;
  reason: string;
  sources: string[];
  scope: Record<string, unknown>;
  metrics: Record<string, unknown>;
  hint: Record<string, unknown> | null;
  notes: string[];
};

export type DecisionEvidence = {
  project_id: string;
  project_name: string;
  buyer: string | null;
  buyer_match_mode: string;
  window_days: number;
  min_samples: Record<string, number>;
  generated_at: string;
  engine_version: string;
  items: DecisionEvidenceItem[];
  missing: string[];
  notes: string[];
};

export type DecisionCard = {
  id: string;
  project_id: string;
  run_no: number;
  engine_mode: string;
  engine_version: string;
  constraint_version: number | null;
  score: number | null;
  computed_status: string;
  status: string;
  human_status: string | null;
  dimensions: DecisionDimension[];
  vetoes: DecisionVeto[];
  thresholds: Record<string, unknown>;
  source_labels: Record<string, unknown>;
  inputs: Record<string, string | null> | null;
  created_at: string;
  confirmed_at: string | null;
  override_reason: string | null;
};

export type BidSection = {
  id: string;
  title: string;
  section_type: string;
  order_index: number;
  scoring_item_id: string | null;
  content_json: Array<Record<string, any>>;
  content_text: string;
  status: string;
  generation_mode: string | null;
  factcheck_status: string;
  traceability_rate: string | number | null;
  unsourced_count: number;
  approved_at: string | null;
};

export type EvidenceLink = {
  claim: string;
  claim_type: string;
  status: string;
  source_type: string | null;
  source_ref: string | null;
  source_quote: string | null;
};

export type GenerationRun = {
  run_id: string;
  status: string;
  sections_total: number;
  sections_generated: number;
  error_code: string | null;
  error_message: string | null;
};

/** AI 用量：篇数与字数，按项目 / 客户维度查询（后端 ai_usage_ledger 聚合）。 */
export type AiUsage = {
  pieces: number;
  words: number;
};

export type Finding = {
  id: string;
  rule_id: string;
  category: string;
  severity: string;
  finding_type: string;
  message: string;
  location: Record<string, unknown>;
  expected: string | null;
  actual: string | null;
  basis: string | null;
  suggestion: string | null;
  auto_fixable: boolean;
  needs_review: boolean;
  status: string;
  evidence_ref: string | null;
  created_at: string;
};

export type ComplianceReport = {
  id: string;
  project_id: string;
  status: string;
  rule_version: string;
  file_hash: string;
  summary_json: {
    hard?: number;
    soft?: number;
    findings?: number;
    by_category?: Record<string, number>;
    rules_version?: string;
  };
  created_at: string;
  findings: Finding[];
};

export type TenantUser = {
  id: string;
  email: string;
  display_name: string;
  status: string;
  roles: string[];
  last_login_at: string | null;
  created_at: string;
};

export type RoleOption = {
  id?: string;
  code: string;
  name: string;
  permissions: string[];
  is_system?: boolean;
  scope_types?: string[];
};

export type KnowledgeBase = {
  id: string;
  scope: "personal" | "tenant" | string;
  owner_id: string | null;
  org_unit_id?: string | null;
  name: string;
  description: string | null;
  classification: string;
  visibility: string;
  status: string;
  acl_version: number;
  created_at: string;
};

export type KnowledgeAclSubjects = {
  users: { id: string; display_name: string; email: string }[];
  org_units: { id: string; name: string; depth: number }[];
};

export type KnowledgeAcl = {
  id: string;
  resource_type: string;
  resource_id: string;
  subject_type: string;
  subject_id: string;
  role: string;
  include_descendants: boolean;
  expires_at: string | null;
  created_at: string;
};

export type KnowledgePack = {
  id: string;
  code: string;
  name: string;
  category: "structure" | "format" | "material" | string;
  description: string | null;
  content_json: Record<string, unknown>;
  version: string;
  is_builtin: boolean;
  enabled: boolean;
  scope?: "system" | "tenant" | "personal" | string;
  owner_id?: string | null;
  classification?: string;
  status?: string;
  published_at?: string | null;
};

export type SkillItem = {
  code: string;
  name: string;
  category: string;
  status: string;
  description: string;
};

export type WorkspaceStage = {
  key: string;
  label: string;
  status: string;
  detail: string;
  blockers: string[];
};

export type WorkspaceInfo = {
  project_id: string;
  template_code: string | null;
  format_code: string | null;
  template_name: string | null;
  format_name: string | null;
  stages: WorkspaceStage[];
  sections_total: number;
  sections_approved: number;
  unsourced_count: number;
};

export type ReadinessStatus = "pending" | "done" | "blocked" | "not_applicable" | "waived";

export type ReadinessItem = {
  id: string;
  item_key: string;
  template_code: string;
  template_version: number;
  category: string;
  label: string;
  source_type: "auto" | "manual";
  rule_id: string | null;
  is_blocking: boolean;
  applicability: string;
  evidence_required: boolean;
  status: ReadinessStatus;
  owner_id: string | null;
  collaborator_ids: string[];
  due_at: string | null;
  completed_at: string | null;
  evidence_refs: string[];
  note: string | null;
  waived_reason: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  source_state: string;
  synced_at: string | null;
  stage: string;
  sort_order: number;
};

export type ReadinessResponse = {
  template: { code: string; name: string; version: number; description: string; item_count?: number | null };
  bid_deadline: string | null;
  internal_deadline: string | null;
  items: ReadinessItem[];
  summary: {
    total: number;
    done: number;
    blocked: number;
    pending: number;
    not_applicable: number;
    waived: number;
    blocking_open: number;
    completion_rate: number;
  };
};

export type MaterialItem = {
  name: string;
  section: string;
  kind: string;
  status: string;
  matches: string[];
  knowledge_matches: string[];
};

export type CompliancePolicy = {
  rule_id: string;
  name: string;
  category: string;
  type: string;
  default_severity: string;
  effective_severity: string;
  enabled: boolean;
  overridden: boolean;
};

export type ModelConfig = {
  purpose: "main" | "judge";
  scope?: "tenant" | "personal" | string;
  provider: string;
  base_url: string | null;
  model: string | null;
  enabled: boolean;
  effective_base_url: string | null;
  effective_model: string | null;
  configured: boolean;
  overridden: boolean;
  key_last4?: string | null;
  credential_scope?: string;
};

export type OrgUnitNode = {
  id: string;
  parent_id: string | null;
  kind: string;
  name: string;
  path: string;
  sort_order: number;
  status: string;
  children: OrgUnitNode[];
};

export type FieldPolicy = {
  id: string;
  resource_type: string;
  field_name: string;
  min_clearance: string;
  mask_strategy: string;
};

export type AccessRequest = {
  id: string;
  resource_type: string;
  resource_id: string;
  requester_id: string;
  approver_id: string | null;
  reason: string;
  status: string;
  requested_minutes: number;
  expires_at: string | null;
  decided_at: string | null;
  created_at: string;
};

export type PersonalResource = {
  resource_type: string;
  id: string;
  name: string;
  owner_id: string | null;
  owner_email: string | null;
  classification: string;
  status: string;
  owner_status: string;
  created_at: string;
  document_count: number;
  size_bytes: number;
  version: string | null;
};

export type AuditEvent = {
  id: string;
  seq: number;
  tenant_id: string;
  target_tenant_id: string | null;
  actor_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  data: Record<string, unknown>;
  created_at: string;
};

export type AuditVerify = {
  valid: boolean;
  events: number;
};

export type KnowledgeDocument = {
  id: string;
  kb_id?: string;
  classification?: string;
  title: string;
  kind: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  status: string;
  tags: string[];
  error_message: string | null;
  text_chars: number;
  created_at: string;
};

export type KnowledgeImageAsset = {
  id: string;
  kb_id?: string;
  classification?: string;
  title: string;
  caption: string | null;
  tags: string[];
  filename: string;
  content_type: string | null;
  size_bytes: number;
  width: number | null;
  height: number | null;
  sensitive: boolean;
  created_at: string;
};

export type KnowledgeChunkHit = {
  document_id: string;
  title: string;
  chunk: string;
  score: number;
};

export type KnowledgeSearchResult = {
  query: string;
  chunks: KnowledgeChunkHit[];
  images: (KnowledgeImageAsset & { score: number })[];
};

export type KnowledgeChunkItem = {
  chunk_index: number;
  heading: string | null;
  text: string;
  keywords: string[];
};

export type KnowledgeDocumentDetail = KnowledgeDocument & {
  chunks: KnowledgeChunkItem[];
  text_preview: string;
};

export type WebSearchItem = {
  title: string;
  url: string;
  snippet: string;
  published_at: string | null;
};

export type WebSearchResponse = {
  status: string;
  provider: string;
  query: string;
  results: WebSearchItem[];
  raw_ref: string;
  charged: boolean;
  fetched_at: string;
};

export type ContractClause = {
  id: string;
  seq: number;
  title: string;
  text: string;
  category: string;
  page: number | null;
  source_span: Record<string, unknown>;
};

export type ContractFinding = {
  id: string;
  clause_id: string | null;
  kind: string;
  rule_id: string | null;
  severity: string;
  message: string;
  legal_basis: string | null;
  evidence_span: Record<string, unknown>;
  source: string;
  status: string;
  confirmed_by: string | null;
  confirmed_at: string | null;
  // 置信度（rule=1.0 / heuristic=0.8 / llm=0.6，证据回查失败 0.4）与来源引擎
  confidence?: string | number | null;
  detection_method?: string | null;
  redline?: string | null;
};

export type ContractReviewTemplate = {
  id: string;
  is_system: boolean;
  contract_type: string;
  name: string;
  contract_type_keywords: string[];
  review_points: string[];
  core_purposes: string[];
  prompt_rules: string[];
  report_sections: string[];
  typical_description: string | null;
  enabled: boolean;
};

export type ContractReview = {
  id: string;
  project_id: string;
  filename: string;
  size_bytes: number;
  file_hash: string;
  status: string;
  summary_json: Record<string, unknown>;
  created_by: string | null;
  created_at: string;
  replaces_review_id?: string | null;
  template_matches?: Record<string, unknown> | null;
  clauses?: ContractClause[];
  findings?: ContractFinding[];
  disclaimer?: string;
};

export type NotificationChannel = {
  id: string;
  kind: "wechat" | "email";
  target: string;
  label: string | null;
  enabled: boolean;
};

export type ApiToken = {
  id: string;
  name: string;
  token_prefix: string;
  scopes: string[];
  created_by: string | null;
  created_at: string;
  expires_at: string | null;
  last_used_at: string | null;
  revoked_at: string | null;
};

export type WebhookEndpoint = {
  id: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_by: string | null;
  created_at: string;
};

export type WebhookDelivery = {
  id: string;
  endpoint_id: string;
  event: string;
  status: string;
  attempts: number;
  response_code: number | null;
  error: string | null;
  created_at: string;
};
