import { useEffect, useMemo, useRef, useState } from "react";
import {
  Button,
  Checkbox,
  InlineNotification,
  TextArea,
  TextInput,
  Tag,
} from "@carbon/react";
import { Add, ArrowRight, Play, Send, Warning } from "@carbon/icons-react";
import { api } from "../api/client";
import type { AgentEvent, AgentReport } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate, type Route } from "../router";
import { dateTime, errorMessage, money, num, tagType, timeAgo } from "../utils";

const RUNNING = new Set(["running", "working", "started", "publishing", "cancelling"]);

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
  const [prompt, setPrompt] = useState("");
  const [repo, setRepo] = useState("");
  const [base, setBase] = useState("");
  const [model, setModel] = useState("vellur-coder");
  const [visual, setVisual] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

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
      });
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
        <div className="nova-form-grid">
          <TextInput
            id="task-repo"
            labelText="Repository (owner/name)"
            placeholder={(projects.data?.self_repo as string | undefined) || "owner/name"}
            value={repo}
            onChange={(event) => setRepo(event.target.value)}
          />
          <TextInput
            id="task-base"
            labelText="Base branch (optional)"
            placeholder="main"
            value={base}
            onChange={(event) => setBase(event.target.value)}
          />
          <div className="nova-field">
            <label htmlFor="task-model">Model role</label>
            <select id="task-model" value={model} onChange={(event) => setModel(event.target.value)}>
              <option value="vellur-coder">vellur-coder · coding</option>
              <option value="vellur-max">vellur-max · hard problems</option>
              <option value="vellur-chat">vellur-chat · economical</option>
            </select>
          </div>
          <Checkbox
            id="task-visual"
            labelText="Ask for visual inspection (uses a vision-capable endpoint)"
            checked={visual}
            onChange={(_: unknown, { checked }: { checked: boolean }) => setVisual(Boolean(checked))}
          />
        </div>
        {projects.data?.projects.length ? (
          <div className="nova-note">
            Known repositories: {projects.data.projects.map((project) => project.repo).join(", ")}
          </div>
        ) : null}
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
      await api.sendTaskMessage(id, follow.trim());
      setFollow("");
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
              <TextArea
                id="task-follow"
                labelText="Follow up"
                hideLabel
                rows={2}
                placeholder="Add a constraint or ask for a change…"
                value={follow}
                onChange={(event) => setFollow(event.target.value)}
              />
              <Button size="sm" renderIcon={Send} disabled={sending || !follow.trim()} onClick={() => void send()}>
                Send
              </Button>
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
