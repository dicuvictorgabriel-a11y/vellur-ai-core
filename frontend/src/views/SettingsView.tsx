import { useEffect, useState } from "react";
import { Button, Checkbox, InlineNotification, Select, TextArea, TextInput } from "@carbon/react";
import { Save, Settings } from "@carbon/icons-react";
import { api } from "../api/client";
import type { AgentState, Status } from "../api/types";
import { LoadError, Loading, PageHeader, SectionTitle } from "../components/Ui";
import { useAsync } from "../hooks";
import { errorMessage } from "../utils";

type SettingsForm = {
  enabled: boolean;
  preferred_provider: string;
  profile: string;
  idle_timeout_min: string;
  monthly_budget_usd: string;
  total_budget_cap_usd: string;
  max_hourly_usd: string;
  max_session_hours: string;
  auto_start_paid: boolean;
  paid_fallback: boolean;
  memory: string;
};

const FALLBACK: SettingsForm = {
  enabled: true,
  preferred_provider: "",
  profile: "coding",
  idle_timeout_min: "20",
  monthly_budget_usd: "50",
  total_budget_cap_usd: "0",
  max_hourly_usd: "0.50",
  max_session_hours: "3",
  auto_start_paid: false,
  paid_fallback: false,
  memory: "",
};

function fromStatus(status: Status): SettingsForm {
  const settings = status.settings || {};
  const value = (key: keyof SettingsForm) => String(settings[key] ?? FALLBACK[key]);
  return {
    enabled: Boolean(settings.enabled ?? true),
    preferred_provider: value("preferred_provider"),
    profile: value("profile"),
    idle_timeout_min: value("idle_timeout_min"),
    monthly_budget_usd: value("monthly_budget_usd"),
    total_budget_cap_usd: value("total_budget_cap_usd"),
    max_hourly_usd: value("max_hourly_usd"),
    max_session_hours: value("max_session_hours"),
    auto_start_paid: Boolean(settings.auto_start_paid),
    paid_fallback: Boolean(settings.paid_fallback),
    memory: String(settings.memory ?? ""),
  };
}

export function SettingsView() {
  const status = useAsync(() => api.status(), []);
  const agent = useAsync(() => api.agent(), []);
  const [form, setForm] = useState<SettingsForm>(FALLBACK);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [token, setToken] = useState("");
  const [repo, setRepo] = useState("");
  const [branch, setBranch] = useState("main");
  const [githubNote, setGithubNote] = useState("");

  useEffect(() => {
    if (status.data) setForm(fromStatus(status.data));
  }, [status.data]);

  const save = async () => {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await api.updateSettings({
        ...form,
        idle_timeout_min: Number(form.idle_timeout_min),
        monthly_budget_usd: Number(form.monthly_budget_usd),
        total_budget_cap_usd: Number(form.total_budget_cap_usd),
        max_hourly_usd: Number(form.max_hourly_usd),
        max_session_hours: Number(form.max_session_hours),
      });
      setNote("Settings saved on the server.");
      status.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const saveToken = async () => {
    if (!token.trim()) {
      setGithubNote("Paste a fine-grained token first.");
      return;
    }
    setGithubNote("");
    try {
      await api.setGithubToken(token.trim());
      setToken("");
      setGithubNote("Token saved. It is write-only and cannot be read back.");
      agent.reload();
    } catch (reason) {
      setGithubNote(errorMessage(reason));
    }
  };

  const checkGithub = async () => {
    setGithubNote("Checking GitHub…");
    try {
      const result = await api.checkGithub(repo.trim(), branch.trim());
      const message = String(result.message || result.error || JSON.stringify(result));
      setGithubNote(message);
    } catch (reason) {
      setGithubNote(errorMessage(reason));
    }
  };

  if (status.loading && !status.data) return <Loading label="Loading settings" />;
  if (status.error) return <LoadError message={status.error} onRetry={status.reload} />;

  const github = agent.data as (AgentState & { github?: { message?: string; login?: string } }) | null;
  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Settings"
        title="Server settings"
        description="The same validated settings the controller owns. Budget limits remain authoritative on the server."
        actions={<Button kind="primary" renderIcon={Save} onClick={() => void save()} disabled={busy}>{busy ? "Saving…" : "Save settings"}</Button>}
      />
      {note ? <InlineNotification kind="success" lowContrast title="Settings" subtitle={note} hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" lowContrast title="Settings rejected" subtitle={error} hideCloseButton /> : null}
      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint="validated by /autopilot/api/settings">Autopilot</SectionTitle>
          <div className="nova-form-grid">
            <Select id="set-provider" labelText="Preferred GPU provider" value={form.preferred_provider} onChange={(event) => setForm({ ...form, preferred_provider: event.target.value })}>
              {(status.data?.providers || []).map((provider) => (
                <option key={provider.provider} value={provider.provider}>{provider.provider}</option>
              ))}
            </Select>
            <Select id="set-profile" labelText="Model profile" value={form.profile} onChange={(event) => setForm({ ...form, profile: event.target.value })}>
              {Object.entries(status.data?.profiles || {}).map(([key, profile]) => (
                <option key={key} value={key}>{profile.label}</option>
              ))}
            </Select>
            <TextInput id="set-idle" labelText="Idle shutdown (minutes)" type="number" min={1} max={240} value={form.idle_timeout_min} onChange={(event) => setForm({ ...form, idle_timeout_min: event.target.value })} />
            <TextInput id="set-session" labelText="Maximum session (hours)" type="number" step="0.25" min={0.25} max={24} value={form.max_session_hours} onChange={(event) => setForm({ ...form, max_session_hours: event.target.value })} />
            <TextInput id="set-monthly" labelText="Monthly budget (USD)" type="number" step="0.01" min={0} value={form.monthly_budget_usd} onChange={(event) => setForm({ ...form, monthly_budget_usd: event.target.value })} />
            <TextInput id="set-cap" labelText="All-time cap (USD; 0 = monthly only)" type="number" step="0.01" min={0} value={form.total_budget_cap_usd} onChange={(event) => setForm({ ...form, total_budget_cap_usd: event.target.value })} />
            <TextInput id="set-hourly" labelText="Maximum hourly spend (USD)" type="number" step="0.01" min={0} value={form.max_hourly_usd} onChange={(event) => setForm({ ...form, max_hourly_usd: event.target.value })} />
          </div>
          <div className="nova-checkstack">
            <Checkbox id="set-enabled" labelText="Autopilot enabled" checked={form.enabled} onChange={(_, checked) => setForm({ ...form, enabled: Boolean(checked) })} />
            <Checkbox id="set-paid-start" labelText="Allow a GPU request to start a paid provider automatically" checked={form.auto_start_paid} onChange={(_, checked) => setForm({ ...form, auto_start_paid: Boolean(checked) })} />
            <Checkbox id="set-fallback" labelText="Allow paid fallback when the preferred provider fails" checked={form.paid_fallback} onChange={(_, checked) => setForm({ ...form, paid_fallback: Boolean(checked) })} />
          </div>
        </section>

        <section className="nova-card nova-span-2">
          <SectionTitle hint={github?.github_token_set ? "token configured" : "write-only"}>GitHub connection</SectionTitle>
          <p className="nova-note">{github?.github?.message || "A fine-grained token is stored on the server and never shown again."}</p>
          <div className="nova-form-grid">
            <TextInput id="gh-token" labelText="Fine-grained GitHub token" type="password" autoComplete="off" value={token} onChange={(event) => setToken(event.target.value)} placeholder="github_pat_…" />
            <TextInput id="gh-repo" labelText="Repository for access check" value={repo} onChange={(event) => setRepo(event.target.value)} placeholder="owner/repository" />
            <TextInput id="gh-branch" labelText="Branch" value={branch} onChange={(event) => setBranch(event.target.value)} />
          </div>
          <div className="nova-row">
            <Button size="sm" onClick={() => void saveToken()}>Save token</Button>
            <Button size="sm" kind="tertiary" onClick={() => void checkGithub()}>Check access</Button>
          </div>
          {githubNote ? <p className="nova-note" role="status">{githubNote}</p> : null}
        </section>

        <section className="nova-card nova-span-2">
          <SectionTitle hint="agent instructions">Project memory</SectionTitle>
          <TextArea
            id="set-memory"
            labelText="Durable owner notes passed to the coding agent"
            rows={7}
            maxLength={4000}
            value={form.memory}
            onChange={(event) => setForm({ ...form, memory: event.target.value })}
          />
          <p className="nova-note"><Settings size={14} /> Secret-looking values are rejected by the server; never paste keys or tokens here.</p>
        </section>
      </div>
    </div>
  );
}
