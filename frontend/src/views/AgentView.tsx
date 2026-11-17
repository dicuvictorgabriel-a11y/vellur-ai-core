import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  InlineNotification,
  Select,
  SelectItem,
  TextArea,
  TextInput,
  Tag,
} from "@carbon/react";
import {
  Add,
  ArrowRight,
  Attachment as AttachmentIcon,
  Checkmark,
  Close,
  Play,
  Send,
  Warning,
} from "@carbon/icons-react";
import { api, uploadAttachment } from "../api/client";
import type {
  AgentBranches,
  AgentEvent,
  AgentReport,
  Attachment as AttachmentInfo,
} from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate, type Route } from "../router";
import { dateTime, errorMessage, money, num, tagType, timeAgo } from "../utils";

const RUNNING = new Set(["running", "working", "started", "publishing", "cancelling"]);

interface PendingAttachment extends AttachmentInfo {
  localName?: string;
}

const MODEL_ROLES = [
  {
    id: "vellur-coder",
    name: "Coder",
    short: "Best for agent tasks",
    description: "Writes, edits and tests code. Start here for almost every repository task.",
  },
  {
    id: "vellur-max",
    name: "Max",
    short: "Strongest reasoning",
    description: "Use when a task is unusually hard or Coder keeps getting stuck.",
  },
  {
    id: "vellur-chat",
    name: "Chat",
    short: "Cheapest",
    description: "Light questions and tiny edits. Less reliable for complex code changes.",
  },
] as const;

export function AgentView({ route }: { route: Route }) {
  if (route.param === "new") return <NewTask />;
  if (route.param) return <TaskDetail id={Number(route.param)} />;
  return <TaskList />;
}

function TaskList() {
  const agent = useAsync(() => api.agent(), []);
  const projects = useAsync(() => api.projects(), []);
  const tasks = agent.data?.tasks ?? [];
  const running = tasks.filter((task) => RUNNING.has(String(task.state))).length;

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Agent"
        title="Coding tasks"
        description="Each task runs in an isolated workspace. State is the process; outcome is the judged result — never an exit code alone."
        actions={
          <Button kind="primary" renderIcon={Add} onClick={() => navigate("agent/new")}>
            New task
          </Button>
        }
      />
      <section className="nova-stat-grid">
        <Stat label="Tasks" value={num(tasks.length)} detail="recent controller history" />
        <Stat label="Running" value={num(running)} detail="isolated workspaces" tone={running ? "ok" : undefined} />
        <Stat label="Projects" value={num(projects.data?.projects.length ?? 0)} detail="repos with activity" />
        <Stat
          label="GitHub"
          value={agent.data?.github_token_set ? "Connected" : "Not connected"}
          detail={(agent.data?.github as { message?: string } | undefined)?.message || "no token saved"}
          tone={agent.data?.github_token_set ? "ok" : "warn"}
        />
      </section>
      {agent.error ? <LoadError message={agent.error} onRetry={agent.reload} /> : null}
      {agent.loading && !agent.data ? <Loading label="Loading tasks" /> : null}
      {!agent.loading && tasks.length === 0 ? (
        <EmptyState title="No tasks yet" detail="Describe a change, choose a repository, and let the agent work." />
      ) : (
        <div className="nova-card">
          <SectionTitle hint={`${tasks.length} total`}>History</SectionTitle>
          <ul className="nova-list">
            {tasks.map((task) => (
              <li key={task.id}>
                <a className="nova-list-row" href={`#/agent/${task.id}`}>
                  <span className="nova-list-main">
                    <strong>{task.prompt || `Task ${task.id}`}</strong>
                    <small>
                      #{task.id} · {task.repo || "no repo"} · {task.model || "model"} · {timeAgo(task.updated)}
                    </small>
                  </span>
                  <Tag type={tagType(task.state)}>{task.state || "unknown"}</Tag>
                  {task.outcome ? <Tag type={tagType(task.outcome)}>{task.outcome}</Tag> : null}
                  <ArrowRight size={16} />
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function NewTask() {
  const agent = useAsync(() => api.agent(), []);
  const projects = useAsync(() => api.projects(), []);
  const repos = useAsync(() => api.repos(), []);
  const llm = useAsync(() => api.llm(), []);
  const [prompt, setPrompt] = useState("");
  const [repo, setRepo] = useState("");
  const [repoMode, setRepoMode] = useState<"list" | "manual">("list");
  const [base, setBase] = useState("");
  const [branchMode, setBranchMode] = useState<"list" | "manual">("list");
  const [branches, setBranches] = useState<AgentBranches | null>(null);
  const [branchLoading, setBranchLoading] = useState(false);
  const [branchError, setBranchError] = useState("");
  const [model, setModel] = useState("vellur-coder");
  const [visual, setVisual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachError, setAttachError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const pickFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setAttachError("");
    try {
      for (const file of Array.from(files)) {
        const uploaded = await uploadAttachment(file);
        setPending((items) => [...items, { ...uploaded, localName: file.name }]);
      }
    } catch (reason) {
      setAttachError(errorMessage(reason));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const removeAttachment = (aid: string) =>
    setPending((items) => items.filter((item) => item.id !== aid));

  const repoChoices = useMemo(() => {
    const seen = new Set<string>();
    const rows: Array<{ value: string; label: string }> = [];
    const add = (value?: string, detail?: string) => {
      const clean = (value || "").trim();
      if (!clean || seen.has(clean)) return;
      seen.add(clean);
      rows.push({ value: clean, label: detail ? `${clean} · ${detail}` : clean });
    };
    add(projects.data?.self_repo, "this repository");
    projects.data?.projects?.forEach((project) =>
      add(project.repo, `${project.tasks} task${project.tasks === 1 ? "" : "s"}`),
    );
    repos.data?.repos?.forEach((item) =>
      add(item.name, item.default_branch ? `default ${item.default_branch}` : undefined),
    );
    return rows;
  }, [projects.data, repos.data]);

  useEffect(() => {
    if (!/^[^/\s]+\/[^/\s]+$/.test(repo)) {
      setBranches(null);
      setBranchError("");
      setBranchLoading(false);
      return;
    }
    let active = true;
    const timer = window.setTimeout(() => {
      setBranchLoading(true);
      setBranchError("");
      api.branches(repo)
        .then((result) => {
          if (!active) return;
          setBranches(result);
          setBase((current) =>
            current && current !== result.default && !result.branches.includes(current) ? "" : current,
          );
        })
        .catch((reason) => {
          if (active) setBranchError(errorMessage(reason));
        })
        .finally(() => {
          if (active) setBranchLoading(false);
        });
    }, 350);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [repo]);

  const chooseRepo = (value: string) => {
    setBranchMode("list");
    setBase("");
    setBranches(null);
    if (value === "__manual__") {
      setRepoMode("manual");
      setRepo("");
      return;
    }
    setRepoMode("list");
    setRepo(value);
  };

  const modelStatus = (role: string) => {
    if (llm.loading) return { tone: "muted", text: "Checking availability…" };
    if (llm.error) return { tone: "muted", text: "Availability unknown" };
    const usable = (llm.data?.effective?.[role] || []).filter((target) => target.usable);
    if (!usable.length) return { tone: "warn", text: "No endpoint configured" };
    return { tone: "ok", text: `Ready · ${usable.length} endpoint${usable.length === 1 ? "" : "s"}` };
  };

  const create = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    setBusy(true);
    setError("");
    try {
      const task = await api.createTask({
        prompt: prompt.trim(),
        model,
        repo: repo.trim(),
        base: base.trim(),
        visual_inspection: visual || undefined,
        attachments: pending.map((item) => item.id),
      });
      setPending([]);
      navigate(`agent/${task.id}`);
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Agent"
        title="New task"
        description="The agent keeps the GitHub token; the runner never receives production credentials."
        actions={<Button kind="ghost" onClick={() => navigate("agent")}>Back to tasks</Button>}
      />
      <form className="nova-card nova-form" onSubmit={create}>
        {error ? <InlineNotification kind="error" lowContrast title="Task not created" subtitle={error} hideCloseButton /> : null}
        {!agent.data?.available ? (
          <InlineNotification
            kind="warning"
            lowContrast
            title="Agent unavailable"
            subtitle={agent.data?.why || "The controller reports the agent runner as unavailable."}
            hideCloseButton
          />
        ) : null}
        <TextArea
          id="task-prompt"
          labelText="What should NOVA do?"
          placeholder="Fix the flaky checkout test and explain the root cause. Run the tests before reporting."
          rows={6}
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          required
        />
        {attachError ? (
          <InlineNotification kind="error" lowContrast title="Upload failed" subtitle={attachError} hideCloseButton />
        ) : null}
        {pending.length ? (
          <div className="nova-attach-list">
            {pending.map((file) => (
              <span key={file.id} className="nova-chip">
                <AttachmentIcon size={12} />
                {file.localName || file.name}
                <button
                  type="button"
                  aria-label={`Remove ${file.localName || file.name}`}
                  onClick={() => removeAttachment(file.id)}
                >
                  <Close size={12} />
                </button>
              </span>
            ))}
          </div>
        ) : null}
        <div className="nova-row">
          <input
            ref={fileRef}
            type="file"
            multiple
            hidden
            onChange={(event) => void pickFiles(event.target.files)}
          />
          <Button
            kind="ghost"
            size="sm"
            renderIcon={AttachmentIcon}
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
          >
            {uploading ? "Uploading…" : "Attach files"}
          </Button>
          <span className="nova-note">Upload a file and it is referenced by the task.</span>
        </div>
        <div className="nova-form-grid">
          <div className="nova-stack">
            <Select
              id="task-repo"
              labelText="Repository"
              value={repoMode === "manual" ? "__manual__" : repo}
              onChange={(event) => chooseRepo(event.target.value)}
              helperText="From GitHub and your existing projects."
            >
              <SelectItem value="" text="No repository (empty workspace)" />
              {repoChoices.map((choice) => (
                <SelectItem key={choice.value} value={choice.value} text={choice.label} />
              ))}
              <SelectItem value="__manual__" text="Other repository…" />
            </Select>
            {repoMode === "manual" ? (
              <TextInput
                id="task-repo-manual"
                labelText="Repository name"
                placeholder="owner/name"
                value={repo}
                onChange={(event) => setRepo(event.target.value)}
              />
            ) : null}
            {repos.error ? (
              <p className="nova-field-hint">
                GitHub repository list unavailable. Choose a known project or “Other repository…”.
              </p>
            ) : null}
          </div>
          <div className="nova-stack">
            <Select
              id="task-base"
              labelText="Base branch"
              value={branchMode === "manual" ? "__manual__" : base}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "__manual__") {
                  setBranchMode("manual");
                  setBase("");
                  return;
                }
                setBranchMode("list");
                setBase(value);
              }}
              helperText={
                branchLoading
                  ? "Loading branches…"
                  : branches
                    ? `${branches.branches.length} branch${branches.branches.length === 1 ? "" : "es"} found.`
                    : "Default branch unless you choose another."
              }
            >
              <SelectItem
                value=""
                text={branches?.default ? `Default branch (${branches.default})` : "Default branch"}
              />
              {(branches?.branches || [])
                .filter((branch) => branch !== branches?.default)
                .map((branch) => (
                  <SelectItem key={branch} value={branch} text={branch} />
                ))}
              <SelectItem value="__manual__" text="Other branch…" />
            </Select>
            {branchMode === "manual" ? (
              <TextInput
                id="task-base-manual"
                labelText="Branch name"
                placeholder={branches?.default || "main"}
                value={base}
                onChange={(event) => setBase(event.target.value)}
              />
            ) : null}
            {branchError ? (
              <p className="nova-field-hint">
                Branch list unavailable; choose the default or “Other branch…” and type it.
              </p>
            ) : null}
          </div>
        </div>
        <fieldset className="nova-modelgroup">
          <legend className="nova-modelgroup-legend">Model role</legend>
          <p className="nova-field-hint nova-modelgroup-intro">
            Choose what the task needs. NOVA resolves the role to the best available endpoint and can fall back
            automatically.
          </p>
          <div className="nova-modelcards">
            {MODEL_ROLES.map((role) => {
              const status = modelStatus(role.id);
              const selected = model === role.id;
              return (
                <label key={role.id} className={`nova-modelcard${selected ? " is-selected" : ""}`}>
                  <input
                    type="radio"
                    name="task-model"
                    value={role.id}
                    checked={selected}
                    onChange={() => setModel(role.id)}
                  />
                  <span className="nova-modelcard-head">
                    <strong>{role.name}</strong>
                    {role.id === "vellur-coder" ? (
                      <Tag size="sm" type="green">
                        Recommended
                      </Tag>
                    ) : null}
                    <Checkmark size={16} className="nova-modelcard-check" aria-hidden="true" />
                  </span>
                  <span className="nova-modelcard-short">{role.short}</span>
                  <span className="nova-modelcard-desc">{role.description}</span>
                  <span className={`nova-modelcard-status is-${status.tone}`}>
                    <span className="nova-dot" aria-hidden="true" />
                    {status.text}
                  </span>
                  <span className="nova-modelcard-id">{role.id}</span>
                </label>
              );
            })}
          </div>
          <p className="nova-field-hint">
            Pick a role; the exact model is set in{" "}
            <button type="button" className="nova-linkbtn" onClick={() => navigate("models")}>
              Models
            </button>
            .
          </p>
        </fieldset>
        <Checkbox
          id="task-visual"
          labelText="Ask for visual inspection (uses a vision-capable endpoint)"
          checked={visual}
          onChange={(_: unknown, { checked }: { checked: boolean }) => setVisual(Boolean(checked))}
        />
        <div className="nova-row">
          <Button type="submit" renderIcon={Play} disabled={busy || !prompt.trim()}>
            {busy ? "Starting…" : "Start task"}
          </Button>
          <span className="nova-note">
            Limits: {(agent.data?.max_minutes as number | undefined) ?? "—"} minutes,{" "}
            {money(agent.data?.max_usd as number | undefined)} per task.
          </span>
        </div>
      </form>
    </div>
  );
}

function TaskDetail({ id }: { id: number }) {
  const task = useAsync(() => api.task(id), [id]);
  const delivery = useAsync(() => api.delivery(id), [id]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [eventError, setEventError] = useState("");
  const [follow, setFollow] = useState("");
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState("");
  const [followAtts, setFollowAtts] = useState<PendingAttachment[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachError, setAttachError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const pickFiles = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true);
    setAttachError("");
    try {
      for (const file of Array.from(files)) {
        const uploaded = await uploadAttachment(file);
        setFollowAtts((items) => [...items, { ...uploaded, localName: file.name }]);
      }
    } catch (reason) {
      setAttachError(errorMessage(reason));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };
  const removeAttachment = (aid: string) =>
    setFollowAtts((items) => items.filter((item) => item.id !== aid));
  const feedRef = useRef<HTMLOListElement>(null);
  // Survives the poll restarting when the task state changes: only new events are fetched and appended.
  const cursorRef = useRef(0);
  useEffect(() => {
    cursorRef.current = 0;
    setEvents([]);
  }, [id]);
  const [following, setFollowing] = useState(true);   // live-follow only while the reader is at the bottom
  const state = String(task.data?.state || "");
  const running = RUNNING.has(state);

  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const pull = async () => {
      const cursor = cursorRef.current;
      try {
        const result = await api.taskEvents(id, cursor);
        if (!active) return;
        if (result.events.length) {
          setEvents((current) => {
            const seen = new Set(current.map((event) => event.id));
            return [...current, ...(result.events as unknown as AgentEvent[]).filter((event) => !seen.has(event.id))];
          });
          cursorRef.current = Number(result.events[result.events.length - 1]?.id ?? cursor);
        }
        setEventError("");
        if (RUNNING.has(String(result.task?.state))) timer = window.setTimeout(pull, 2500);
      } catch (reason) {
        if (active) setEventError(errorMessage(reason));
      }
    };
    void pull();
    return () => {
      active = false;
      if (timer) window.clearTimeout(timer);
    };
  }, [id, task.data?.state]);

  // Scroll only the feed's own container, never the page, and only while following the newest events.
  useEffect(() => {
    const feed = feedRef.current;
    if (feed && following) feed.scrollTop = feed.scrollHeight;
  }, [events.length, following]);
  const onFeedScroll = () => {
    const feed = feedRef.current;
    if (!feed) return;
    const atBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 48;
    setFollowing((current) => (current === atBottom ? current : atBottom));
  };

  const report = useMemo<AgentReport | null>(() => {
    const explicit = task.data?.report;
    if (explicit) return explicit;
    const event = events.find((item) => item.kind === "report");
    return event?.data ? (event.data as unknown as AgentReport) : null;
  }, [task.data?.report, events]);

  const [publishing, setPublishing] = useState("");
  const [publishError, setPublishError] = useState("");
  const publish = async (direct: boolean) => {
    const t = task.data;
    if (!t || publishing) return;
    const where = direct ? `directly to ${t.base || "the base branch"}` : "as a new draft pull request";
    if (!window.confirm(`Publish ${t.files_changed ?? "the"} changed files ${where}? Review the file list first.`)) return;
    setPublishing(direct ? "push" : "pr");
    setPublishError("");
    try {
      await (direct ? api.pushTask(id) : api.publishTask(id));
      task.reload();
      delivery.reload();
    } catch (reason) {
      setPublishError(errorMessage(reason));
    } finally {
      setPublishing("");
    }
  };

  const send = async () => {
    if (!follow.trim() || sending) return;
    setSending(true);
    setSendError("");
    try {
      await api.sendTaskMessage(id, follow.trim(), followAtts.map((item) => item.id));
      setFollow("");
      setFollowAtts([]);
      task.reload();
    } catch (reason) {
      setSendError(errorMessage(reason));
    } finally {
      setSending(false);
    }
  };

  if (task.loading && !task.data) return <Loading label="Loading task" />;
  if (task.error) return <LoadError message={task.error} onRetry={task.reload} />;
  const t = task.data!;

  return (
    <div className="nova-view">
      <PageHeader
        kicker={`NOVA · Agent · task #${id}`}
        title={t.prompt || `Task ${id}`}
        description={`${t.repo || "no repository"} · ${t.model || "model"} · updated ${dateTime(t.updated)}`}
        actions={
          <>
            <Tag type={tagType(t.state)}>{t.state || "unknown"}</Tag>
            {t.outcome ? <Tag type={tagType(t.outcome)}>{t.outcome}</Tag> : null}
            <Button kind="ghost" onClick={() => navigate("agent")}>All tasks</Button>
          </>
        }
      />
      <section className="nova-stat-grid">
        <Stat label="State" value={t.state || "unknown"} detail={running ? "process is alive" : "process ended"} tone={running ? "ok" : undefined} />
        <Stat label="Outcome" value={t.outcome || "unjudged"} detail={t.why || "no judgement yet"} tone={t.outcome === "completed" ? "ok" : t.outcome ? "warn" : undefined} />
        <Stat label="Cost" value={money(t.cost_usd)} detail="judged task cost" />
        <Stat
          label="Started"
          value={timeAgo(t.started as number | undefined)}
          detail={t.ended ? `ended ${timeAgo(t.ended as number)}` : "still running"}
        />
      </section>
      <section className="nova-card nova-publish" aria-label="Publish to GitHub">
        <SectionTitle hint={t.can_publish ? `${t.files_changed ?? 0} changed files` : t.publish_why || "not available"}>GitHub</SectionTitle>
        <div className="nova-actions">
          <Button kind="primary" size="sm" disabled={!t.can_publish || !!publishing} onClick={() => void publish(false)}>
            {publishing === "pr" ? "Creating pull request…" : t.pr_url ? "Retry pull request" : "Create pull request"}
          </Button>
          <Button kind="tertiary" size="sm" disabled={!t.can_publish || !!publishing} onClick={() => void publish(true)}>
            {publishing === "push" ? "Pushing…" : `Push to ${t.base || "base branch"}`}
          </Button>
          {t.pr_url ? <a href={t.pr_url} target="_blank" rel="noreferrer">Open pull request</a> : null}
        </div>
        {!t.can_publish && t.publish_why ? <p className="nova-note">Unavailable: {t.publish_why}.</p> : null}
        {publishError ? <InlineNotification kind="error" lowContrast title="GitHub" subtitle={publishError} hideCloseButton /> : null}
      </section>
      {eventError ? <InlineNotification kind="warning" lowContrast title="Event stream" subtitle={eventError} hideCloseButton /> : null}
      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={`${events.length} events`}>Live timeline</SectionTitle>
          <ol className="nova-events" ref={feedRef} onScroll={onFeedScroll} data-testid="agent-feed">
            {events.map((event) => (
              <EventRow key={event.id} event={event} />
            ))}
            {events.length === 0 ? <li className="nova-note">No events recorded.</li> : null}
          </ol>
          {!following && events.length ? (
            <Button kind="tertiary" size="sm" className="nova-jump-latest" onClick={() => setFollowing(true)}>
              Jump to latest
            </Button>
          ) : null}
          {running ? (
            <div className="nova-follow">
              {attachError ? (
                <InlineNotification kind="error" lowContrast title="Upload failed" subtitle={attachError} hideCloseButton />
              ) : null}
              {followAtts.length ? (
                <div className="nova-attach-list">
                  {followAtts.map((file) => (
                    <span key={file.id} className="nova-chip">
                      <AttachmentIcon size={12} />
                      {file.localName || file.name}
                      <button
                        type="button"
                        aria-label={`Remove ${file.localName || file.name}`}
                        onClick={() => removeAttachment(file.id)}
                      >
                        <Close size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              ) : null}
              <TextArea
                id="task-follow"
                labelText="Follow up"
                hideLabel
                rows={2}
                placeholder="Add a constraint, ask for a change, or attach a file…"
                value={follow}
                onChange={(event) => setFollow(event.target.value)}
              />
              <div className="nova-row">
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  hidden
                  onChange={(event) => void pickFiles(event.target.files)}
                />
                <Button
                  kind="ghost"
                  size="sm"
                  renderIcon={AttachmentIcon}
                  disabled={uploading}
                  onClick={() => fileRef.current?.click()}
                >
                  {uploading ? "Uploading…" : "Attach files"}
                </Button>
                <Button size="sm" renderIcon={Send} disabled={sending || !follow.trim()} onClick={() => void send()}>
                  Send
                </Button>
              </div>
            </div>
          ) : null}
          {sendError ? <InlineNotification kind="error" lowContrast title="Message failed" subtitle={sendError} hideCloseButton /> : null}
        </section>

        <section className="nova-card">
          <SectionTitle hint={delivery.data?.stages ? `${delivery.data.stages.filter((stage) => stage.state === "done").length} confirmed` : ""}>
            Delivery
          </SectionTitle>
          {delivery.loading && !delivery.data ? <Loading label="Reading delivery" /> : null}
          {delivery.data?.stages ? (
            <ul className="nova-stage-list">
              {delivery.data.stages.map((stage) => (
                <li key={stage.key}>
                  <Tag type={tagType(stage.state)}>{stage.state}</Tag>
                  <span>{stage.label}</span>
                  {stage.detail ? <small>{stage.detail}</small> : null}
                </li>
              ))}
            </ul>
          ) : (
            <p className="nova-note">Delivery stages are unknown until the installer reports them.</p>
          )}
          {t.pr_url ? (
            <a className="nova-link" href={t.pr_url} target="_blank" rel="noreferrer">
              Open pull request
            </a>
          ) : null}
        </section>

        {report ? (
          <section className="nova-card nova-span-2">
            <SectionTitle hint={report.outcome ? <Tag type={tagType(report.outcome)}>{report.outcome}</Tag> : undefined}>Report</SectionTitle>
            <p>{report.why || "No judgement text."}</p>
            {report.tests?.length ? (
              <div className="nova-table-wrap">
                <table className="nova-table">
                  <thead>
                    <tr>
                      <th>Test command</th>
                      <th>Exit</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.tests.map((test, index) => (
                      <tr key={index}>
                        <td><code>{test.command}</code></td>
                        <td>{test.exit}</td>
                        <td>{test.status || (test.exit === 0 ? "passed" : "failed")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {report.files?.length ? (
              <ul className="nova-file-list">
                {report.files.map((file) => (
                  <li key={file.path}>
                    <code>{file.path}</code>
                    <span>
                      {file.status} +{file.added} −{file.removed}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
            {report.errors?.length ? (
              <InlineNotification
                kind="error"
                lowContrast
                title="Reported errors"
                subtitle={report.errors.join(" · ")}
                hideCloseButton
              />
            ) : null}
          </section>
        ) : null}
      </div>
      <p className="nova-note">
        Checkpoints, diff review, screenshots, 3D assets and publication controls remain in the classic dashboard.
      </p>
    </div>
  );
}

function EventRow({ event }: { event: AgentEvent }) {
  const data = event.data || {};
  const kind = String(event.kind || event.type || "event");
  const text = String(data.text || data.message || data.title || "");
  const tool = data.tool ? String(data.tool) : "";
  const command = (data.input as Record<string, unknown> | undefined)?.command;
  const exit = data.exit;
  const status = data.status ? String(data.status) : "";
  return (
    <li className={`nova-event is-${kind}`}>
      <div className="nova-event-head">
        <Tag type={kind === "error" ? "red" : kind === "tool" ? "gray" : kind === "report" ? "teal" : "blue"}>{kind}</Tag>
        {tool ? <strong>{tool}</strong> : null}
        {status ? <span className="nova-event-status">{status}</span> : null}
        {exit !== undefined ? <span className="nova-event-status">exit {String(exit)}</span> : null}
        <time>{dateTime(event.ts)}</time>
      </div>
      {text ? <div className="nova-event-text">{text}</div> : null}
      {command ? <pre className="nova-pre">{String(command)}</pre> : null}
      {data.output ? <pre className="nova-pre">{String(data.output).slice(0, 4000)}</pre> : null}
      {kind === "error" ? <Warning size={16} /> : null}
    </li>
  );
}
