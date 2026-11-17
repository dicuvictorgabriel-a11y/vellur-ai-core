export type UnknownRecord = Record<string, unknown>;

export interface Me {
  logged_in: boolean;
  must_change?: boolean;
  password_set?: boolean;
  preview?: boolean;
}

export interface ConversationSummary {
  id: number;
  title: string;
  model: string;
  created: number;
  updated: number;
}

export interface Attachment {
  id: string;
  name: string;
  mime?: string;
  size?: number;
  kind?: string;
}

export interface ChatMessage {
  id: number;
  conv_id: number;
  role: "user" | "assistant" | "system" | string;
  content: string;
  created: number;
  meta?: {
    attachments?: Attachment[];
    images?: Array<{ id?: string; url?: string; rel?: string; alt?: string }>;
    model?: string;
    target?: string;
    cost_usd?: number;
    [key: string]: unknown;
  };
}

export interface ConversationDetail extends ConversationSummary {
  messages: ChatMessage[];
  task_id?: number | null;
}

export interface Budget {
  month: string;
  budget_usd: number;
  total_usd: number;
  confirmed_usd: number;
  estimated_unconfirmed_usd: number;
  remaining_usd: number;
  percent: number;
  exhausted: boolean;
  all_in_total_usd?: number;
  fixed_outside_budget_usd?: number;
  note?: string;
  [key: string]: unknown;
}

export interface SessionInfo {
  provider?: string;
  profile?: string;
  gpu?: string;
  gpu_name?: string;
  price_per_hour?: number;
  rate_usd_per_hour?: number;
  address?: string;
  ssh_host?: string;
  ssh_port?: number;
  started_at?: number;
  vram_gb?: number;
  [key: string]: unknown;
}

export interface ProviderInfo {
  provider: string;
  configured: boolean;
  detail: string;
  automated: boolean;
}

export interface Status {
  phase: string;
  live_mode: boolean;
  enabled: boolean;
  session: SessionInfo | null;
  explain: string;
  provider: { name: string; display: string; automated: boolean };
  inflight: number;
  queued: number;
  last_activity?: number;
  idle_shutdown_in_s?: number | null;
  protected_until?: number | null;
  termination?: string | null;
  budget: Budget;
  balances?: Record<string, unknown>;
  providers: ProviderInfo[];
  profiles: Record<string, { label: string; model: string; min_vram_gb: number; context: number; verified: string }>;
  settings: Record<string, unknown> & {
    idle_timeout_min?: number;
    preferred_provider?: string;
    profile?: string;
    auto_start_paid?: boolean;
    [key: string]: unknown;
  };
}

export interface CostRow {
  target: string;
  requests: number;
  ok: number;
  in_tokens: number;
  out_tokens: number;
  cost_usd: number;
  avg_ms: number;
  cost_source: string;
}

export interface CostDay {
  day: string;
  requests: number;
  cost_usd: number;
  in_tokens: number;
  out_tokens: number;
}

export interface Costs {
  month: string;
  budget: Budget;
  by_target: CostRow[];
  by_day: CostDay[];
  by_use: Array<{ name: string; usd: number; in_budget?: boolean }>;
  fixed_costs: Array<{ name?: string; usd?: number; amount_usd?: number; [key: string]: unknown }>;
  subscriptions: Array<{ name?: string; usd?: number; amount_usd?: number; [key: string]: unknown }>;
  efficiency?: UnknownRecord;
  token_efficiency?: UnknownRecord;
}

export interface LlmTarget {
  target: string;
  usable: boolean;
  code: string;
  why: string;
  price: [number, number];
  [key: string]: unknown;
}

export interface LlmState {
  roles: Record<string, string>;
  routes: Record<string, string[]>;
  effective: Record<string, LlmTarget[]>;
  providers: Array<{ name: string; display?: string; configured?: boolean; [key: string]: unknown }>;
  catalog: Array<{ target: string; name?: string; provider?: string; [key: string]: unknown }>;
  budget_exhausted?: boolean;
  [key: string]: unknown;
}

export interface TimelineEvent {
  id: number;
  event_id?: string;
  project?: string;
  task_id?: number | null;
  conv_id?: number | null;
  occurred_at: number;
  source?: string;
  event_type?: string;
  category?: string;
  title: string;
  detail?: string;
  outcome?: string;
  data?: UnknownRecord;
  [key: string]: unknown;
}

export interface Timeline {
  events: TimelineEvent[];
  categories?: string[];
  synced_at?: number;
  added?: number;
}

export interface AgentModelToken {
  target: string;
  label?: string;
  role?: string;
  ok?: boolean;
  [key: string]: unknown;
}

export interface AgentTask {
  id: number;
  project?: string;
  repo?: string;
  prompt?: string;
  state?: string;
  outcome?: string | null;
  why?: string;
  pr_url?: string;
  branch?: string;
  model?: string;
  created?: number;
  updated?: number;
  started?: number;
  finished?: number;
  cost_usd?: number;
  report?: AgentReport | null;
  [key: string]: unknown;
}

export interface AgentReport {
  run?: number;
  outcome?: string;
  why?: string;
  sections?: Record<string, string>;
  tests?: Array<{ command: string; exit: number; status?: string; test?: boolean }>;
  files?: Array<{ path: string; status: string; added: number; removed: number }>;
  shots?: Array<{ name: string; [key: string]: unknown }>;
  errors?: string[];
  [key: string]: unknown;
}

export interface AgentState {
  available: boolean;
  why: string;
  tasks: AgentTask[];
  models?: AgentModelToken[];
  github_token_set?: boolean;
  [key: string]: unknown;
}

export interface AgentEvent {
  id: number;
  task_id?: number;
  ts?: number;
  kind?: string;
  type?: string;
  data?: UnknownRecord;
  [key: string]: unknown;
}

export interface ProjectInfo {
  repo: string;
  name: string;
  tasks: number;
  updated: number;
  last?: AgentTask | null;
  cost?: { confirmed_usd: number; estimated_usd: number; unpriced_requests: number };
  knowledge?: number;
}

export interface Projects {
  projects: ProjectInfo[];
  installed?: string;
  self_repo?: string;
}

export interface KnowledgeRecord {
  id: number;
  namespace?: string;
  key?: string;
  title?: string;
  body?: string;
  status?: string;
  tags?: string;
  paths?: string;
  project?: string;
  source?: string;
  version?: number;
  locked?: boolean | number;
  updated?: number;
  [key: string]: unknown;
}

export interface Knowledge {
  records: KnowledgeRecord[];
  namespaces?: string[];
  project?: string;
  [key: string]: unknown;
}

export interface MissionItem {
  id: number;
  title: string;
  detail?: string;
  state?: string;
  plan?: string;
  repo?: string;
  base?: string;
  blocked_reason?: string;
  cost_estimate_usd?: number | null;
  depends_on?: number | null;
  objective_id?: number | null;
  agent_task_id?: number | null;
  [key: string]: unknown;
}

export interface MissionObjective {
  id: number;
  title: string;
  detail?: string;
  state?: string;
  cost_estimate_usd?: number | null;
  [key: string]: unknown;
}

export interface Mission {
  objective?: MissionObjective | null;
  parked_objectives?: MissionObjective[];
  items?: MissionItem[];
  next?: MissionItem | null;
  [key: string]: unknown;
}

export interface MusicTrack {
  id: number;
  release_id: number;
  title: string;
  duration: string;
  notes?: string;
  position?: number;
  [key: string]: unknown;
}

export interface MusicRelease {
  id: number;
  title: string;
  artist: string;
  status: string;
  target_date: string;
  released_at?: string;
  notes?: string;
  cover_image_id?: string;
  created?: number;
  updated?: number;
  tracks: MusicTrack[];
}

export interface MusicState {
  profile: { artist_name?: string; genre?: string; bio?: string; links?: string; [key: string]: unknown };
  releases: MusicRelease[];
}

export interface ReleaseState {
  approved: string;
  approved_at: number;
  request: string;
  installed: string;
  previous: string;
  last: string;
  seen: number;
  repo: string;
  branch: string;
  installer_active: boolean;
  latest: { sha?: string; message?: string; date?: string; [key: string]: unknown } | null;
  error: string;
}

export interface Delivery {
  task?: AgentTask;
  stages?: Array<{ key: string; label: string; state: string; detail?: string }>;
  [key: string]: unknown;
}
