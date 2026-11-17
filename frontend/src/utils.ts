export function money(value: number | null | undefined, digits = 2): string {
  const n = Number(value ?? 0);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: digits });
}

export function num(value: number | null | undefined): string {
  return Number(value ?? 0).toLocaleString("en-US");
}

export function percent(value: number | null | undefined): string {
  return `${Number(value ?? 0).toFixed(1)}%`;
}

export function dateTime(value: number | null | undefined): string {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function dateOnly(value: number | string | null | undefined): string {
  if (!value) return "—";
  const d = typeof value === "number" ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}

export function timeAgo(value: number | null | undefined): string {
  if (!value) return "—";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - value));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h ${m}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}

export function shortSha(sha: string | null | undefined): string {
  return (sha || "").slice(0, 8) || "—";
}

export function tagType(value: string | null | undefined): "green" | "red" | "blue" | "gray" | "purple" | "cyan" | "magenta" | "teal" | "warm-gray" {
  const v = (value || "").toLowerCase();
  if (["done", "completed", "released", "ok", "running", "active", "approved", "installed", "available"].includes(v)) return "green";
  if (["failed", "error", "blocked", "exhausted", "offline", "stopped"].includes(v)) return "red";
  if (["working", "started", "connecting", "pending", "planned"].includes(v)) return "blue";
  if (["queued", "paused", "unknown", "idle", "partial", "needs-review"].includes(v)) return "purple";
  if (["published", "verified", "ready"].includes(v)) return "teal";
  return "gray";
}

export function errorMessage(reason: unknown): string {
  if (reason instanceof Error) return reason.message;
  if (typeof reason === "string") return reason;
  return "Unexpected error";
}

// Static media is served by the controller; through the VELLUR preview proxy the same bytes live under
// /tasks/<id>/preview/. Fetch calls are rewritten by the preview, <img> and <a download> are not.
export function assetUrl(url: string | null | undefined): string {
  if (!url) return "";
  if (!url.startsWith("/autopilot/")) return url;
  const match = window.location.pathname.match(/^(\/tasks\/\d+\/preview\/)/);
  return match ? `${match[1]}${url.replace(/^\//, "")}` : url;
}
