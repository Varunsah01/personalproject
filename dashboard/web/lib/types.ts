export interface OutreachRow {
  id: string;
  company: string;
  role_url: string;
  role_title: string;
  role_tier: string;
  person_name: string;
  person_title: string;
  person_linkedin: string;
  person_country: string;
  relationship_type: string;
  email: string;
  email_confidence: string;
  linkedin_only: string;
  hook: string;
  subject: string;
  body_path: string;
  status: string;
  assigned_inbox: string;
  send_at_utc: string;
  sent_at_utc: string;
  replied: string;
  notes: string;
  last_updated: string;
  synced_at: string;
  // computed
  days_in_column?: number;
  stale?: boolean;
}

export interface DraftRow extends OutreachRow {
  body_text: string;
  manager_score: number | null;
}

export interface AgentLog {
  id: number;
  timestamp: string;
  agent: string;
  action: string;
  target: string;
  message: string;
  status: "ok" | "warn" | "error";
  row_id: string | null;
  run_date: string;
}

export interface PipelineResponse {
  columns: Record<string, OutreachRow[]>;
  counts: Record<string, number>;
  lastSync: string;
}

export interface DraftsResponse {
  drafts: DraftRow[];
}

export interface LinkedInQueueResponse {
  rows: DraftRow[];
}

export interface LogsResponse {
  logs: AgentLog[];
  total: number;
}

export interface StatsResponse {
  funnel: Record<string, number>;
  lastSync: string;
  draftsPending: number;
  staleCount: number;
}

export interface DecisionRequest {
  action: "approve" | "reject";
  reason?: string;
  notes?: string;
}

export interface ChatMessage {
  ts: string;
  role: "user" | "assistant";
  content: string;
  agent: string | null;
}

export interface ChatHistoryResponse {
  messages: ChatMessage[];
}

// Analytics types

export interface FunnelBar {
  status: string;
  count: number;
}

export interface TierReplyRate {
  tier: string;
  sent: number;
  replied: number;
  rate: number;
}

export interface HookSource {
  domain: string;
  count: number;
  replied: number;
  rate: number;
}

export interface InboxUsage {
  inbox: string;
  sent: number;
  cap: number;
}

export interface AnalyticsResponse {
  funnel7d: FunnelBar[];
  replyRate: TierReplyRate[];
  topHooks: HookSource[];
  inboxUsage: InboxUsage[];
}

export const PIPELINE_STATUSES = [
  "research_done",
  "people_found",
  "contact_found",
  "linkedin_queue",
  "drafted",
  "queued",
  "sent",
  "replied",
] as const;

export type PipelineStatus = (typeof PIPELINE_STATUSES)[number];

export const STATUS_LABELS: Record<PipelineStatus, string> = {
  research_done: "research",
  people_found: "people",
  contact_found: "contact",
  linkedin_queue: "linkedin",
  drafted: "drafted",
  queued: "queued",
  sent: "sent",
  replied: "replied",
};
