import type {
  AgentBranches,
  AgentRepos,
  AgentState,
  AgentTask,
  Attachment,
  ConversationDetail,
  ConversationSummary,
  Costs,
  Delivery,
  Knowledge,
  KnowledgeRecord,
  LlmState,
  Me,
  Mission,
  MusicRelease,
  MusicState,
  MusicTrack,
  Projects,
  ReleaseState,
  Status,
  Timeline,
} from "./types";

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || "/autopilot/api/";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(message: string, status = 0, code = "error") {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type Json = Record<string, unknown>;

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method || "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData) && !(init.body instanceof Blob)) {
    headers.set("Content-Type", "application/json");
  }
  if (method !== "GET" && method !== "HEAD") headers.set("X-Vellur-Request", "1");
  const response = await fetch(API_BASE + path.replace(/^\/+/, ""), {
    ...init,
    headers,
    credentials: "same-origin",
    cache: method === "GET" ? "no-store" : init.cache,
  });
  const text = await response.text();
  let body: Json = {};
  if (text) {
    try {
      body = JSON.parse(text) as Json;
    } catch {
      body = {};
    }
  }
  if (!response.ok) {
    const detail = typeof body.detail === "string" ? body.detail : "";
    const message = String(body.message || detail || `HTTP ${response.status}`);
    throw new ApiError(message, response.status, String(body.error || detail || "error"));
  }
  return body as T;
}

export const api = {
  me: () => request<Me>("auth/me"),
  login: (password: string) =>
    request<{ ok: boolean; must_change?: boolean }>("auth/login", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),
  logout: () => request<{ ok: boolean }>("auth/logout", { method: "POST" }),
  changePassword: (current: string, next: string) =>
    request<{ ok: boolean }>("auth/change", { method: "POST", body: JSON.stringify({ current, new: next }) }),

  status: () => request<Status>("status"),
  publicStatus: () => request<Status>("public/status"),

  conversations: () => request<{ conversations: ConversationSummary[] }>("conversations"),
  conversation: (id: number) => request<ConversationDetail>(`conversations/${id}`),
  renameConversation: (id: number, title: string) =>
    request<ConversationSummary>(`conversations/${id}`, { method: "PATCH", body: JSON.stringify({ title }) }),
  deleteConversation: (id: number) =>
    request<{ ok: boolean }>(`conversations/${id}`, { method: "DELETE" }),
  newConversation: (body: { title?: string; model?: string } = {}) =>
    request<{ id: number } & ConversationSummary>("conversations", { method: "POST", body: JSON.stringify(body) }),

  costs: () => request<Costs>("costs"),
  timeline: (query = "") => request<Timeline>(`timeline${query ? `?${query}` : ""}`),
  llm: () => request<LlmState>("llm"),
  updateRoutes: (routes: Record<string, string[]>) =>
    request<LlmState>("llm/routes", { method: "PUT", body: JSON.stringify(routes) }),
  providers: () => request<Json>("llm/providers"),
  testModel: (target: string) =>
    request<Json>("llm/test", { method: "POST", body: JSON.stringify({ target }) }),

  agent: () => request<AgentState>("agent"),
  repos: () => request<AgentRepos>("agent/repos"),
  branches: (repo: string) => request<AgentBranches>(`agent/branches?repo=${encodeURIComponent(repo)}`),
  task: (id: number) => request<AgentTask>(`agent/tasks/${id}`),
  createTask: (body: Json) => request<AgentTask>("agent/tasks", { method: "POST", body: JSON.stringify(body) }),
  sendTaskMessage: (id: number, text: string) =>
    request<Json>(`agent/tasks/${id}/messages`, { method: "POST", body: JSON.stringify({ text }) }),
  taskDiff: (id: number) => request<{ diff?: string; [key: string]: unknown }>(`agent/tasks/${id}/diff`),
  publishTask: (id: number) => request<AgentTask>(`agent/tasks/${id}/publish`, { method: "POST", body: "{}" }),
  pushTask: (id: number) => request<AgentTask>(`agent/tasks/${id}/publish-direct`, { method: "POST", body: "{}" }),
  delivery: (id: number) => request<Delivery>(`agent/tasks/${id}/delivery`),
  release: () => request<ReleaseState>("release"),

  projects: () => request<Projects>("projects"),
  knowledge: (project: string, query = "") =>
    request<Knowledge>(`knowledge?project=${encodeURIComponent(project)}${query ? `&q=${encodeURIComponent(query)}` : ""}`),
  createKnowledge: (body: Json) =>
    request<KnowledgeRecord>("knowledge", { method: "POST", body: JSON.stringify(body) }),
  updateKnowledge: (id: number, body: Json) =>
    request<KnowledgeRecord>(`knowledge/${id}`, { method: "PUT", body: JSON.stringify(body) }),
  deleteKnowledge: (id: number) =>
    request<{ ok: boolean }>(`knowledge/${id}`, { method: "DELETE" }),
  lockKnowledge: (id: number, locked: boolean) =>
    request<KnowledgeRecord>(`knowledge/${id}/lock`, { method: "POST", body: JSON.stringify({ locked }) }),
  mission: () => request<Mission>("mission"),
  saveObjective: (body: Json) =>
    request<Json>("mission/objective", { method: "POST", body: JSON.stringify(body) }),
  parkObjective: (id?: number) =>
    request<Json>("mission/objective/park", { method: "POST", body: JSON.stringify({ id }) }),
  createMissionItem: (body: Json) =>
    request<Json>("mission/items", { method: "POST", body: JSON.stringify(body) }),
  updateMissionItem: (id: number, body: Json) =>
    request<Json>(`mission/items/${id}`, { method: "POST", body: JSON.stringify(body) }),

  music: () => request<MusicState>("music"),
  musicRelease: (id: number) => request<MusicRelease>(`music/releases/${id}`),
  createRelease: (body: Json) =>
    request<MusicRelease>("music/releases", { method: "POST", body: JSON.stringify(body) }),
  updateRelease: (id: number, body: Json) =>
    request<MusicRelease>(`music/releases/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteRelease: (id: number) =>
    request<{ ok: boolean }>(`music/releases/${id}`, { method: "DELETE" }),
  addTrack: (releaseId: number, body: Json) =>
    request<MusicTrack>(`music/releases/${releaseId}/tracks`, { method: "POST", body: JSON.stringify(body) }),
  updateTrack: (id: number, body: Json) =>
    request<MusicTrack>(`music/tracks/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteTrack: (id: number) =>
    request<{ ok: boolean }>(`music/tracks/${id}`, { method: "DELETE" }),
  saveMusicProfile: (body: Json) =>
    request<{ ok: boolean }>("music/profile", { method: "PUT", body: JSON.stringify(body) }),

  startGpu: (provider?: string) =>
    request<{ session: unknown }>("start", { method: "POST", body: JSON.stringify({ provider }) }),
  stopGpu: () => request<{ session: unknown }>("stop", { method: "POST", body: "{}" }),
  protectGpu: (minutes: number) =>
    request<{ protected_until: number }>("protect", { method: "POST", body: JSON.stringify({ minutes }) }),
  taskEvents: (id: number, after = 0) =>
    request<{ events: Array<Record<string, unknown>>; task: AgentTask; has_more: boolean }>(
      `agent/tasks/${id}/events?after=${after}`,
    ),

  updateSettings: (changes: Json) =>
    request<Json>("settings", { method: "PUT", body: JSON.stringify(changes) }),
  setGithubToken: (token: string) =>
    request<{ github_token_set: boolean; github: Json }>("agent/github", {
      method: "PUT",
      body: JSON.stringify({ token }),
    }),
  checkGithub: (repo: string, branch: string) =>
    request<Json>(`agent/github/check?repo=${encodeURIComponent(repo)}&branch=${encodeURIComponent(branch)}`),
  approveRelease: (sha: string) =>
    request<Json>("release/approve", { method: "POST", body: JSON.stringify({ sha }) }),
  cancelRelease: () => request<Json>("release/cancel", { method: "POST", body: "{}" }),
  rollbackRelease: () => request<Json>("release/rollback", { method: "POST", body: "{}" }),
};

export async function uploadAttachment(file: File): Promise<Attachment> {
  const response = await fetch(API_BASE + "attachments", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "X-Vellur-Request": "1",
      "X-Filename": file.name,
      "Content-Type": file.type || "application/octet-stream",
    },
    body: file,
  });
  const body = (await response.json().catch(() => ({}))) as Json;
  if (!response.ok) throw new ApiError(String(body.message || `HTTP ${response.status}`), response.status);
  return body as unknown as Attachment;
}

export interface StreamHandlers {
  onDelta: (text: string) => void;
  onDone?: (done: Json) => void;
  onError?: (message: string) => void;
  onStatus?: (text: string) => void;
}

// The controller streams OpenAI-compatible chunks as Server-Sent Events over a POST fetch.
export async function streamChat(
  conversationId: number,
  body: { content: string; model?: string; attachments?: string[] },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(API_BASE + `conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "same-origin",
    signal,
    headers: {
      "Content-Type": "application/json",
      "X-Vellur-Request": "1",
      Accept: "text/event-stream",
    },
    body: JSON.stringify(body),
  });
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => "");
    let message = `HTTP ${response.status}`;
    try {
      const parsed = JSON.parse(text) as Json;
      message = String(parsed.message || parsed.detail || message);
    } catch {
      /* plain response */
    }
    throw new ApiError(message, response.status);
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const raw of lines) {
      const line = raw.trim();
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === "[DONE]") continue;
      let data: Json;
      try {
        data = JSON.parse(payload) as Json;
      } catch {
        continue;
      }
      if (data.vellur_error) {
        handlers.onError?.(String(data.vellur_error));
        finished = true;
        continue;
      }
      if (data.vellur_done) {
        handlers.onDone?.(data);
        finished = true;
        continue;
      }
      if (data.vellur_status) {
        handlers.onStatus?.(String(data.vellur_status));
        continue;
      }
      const choices = data.choices as Array<{ delta?: { content?: string }; message?: { content?: string } }> | undefined;
      const delta = choices?.[0]?.delta?.content ?? choices?.[0]?.message?.content;
      if (delta) handlers.onDelta(delta);
    }
  }
  if (!finished) handlers.onDone?.({});
}
