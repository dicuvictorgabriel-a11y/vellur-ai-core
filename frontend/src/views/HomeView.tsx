import { Button, Tag } from "@carbon/react";
import { Add, ArrowRight, Bot, Chat, Meter, Money, Play } from "@carbon/icons-react";
import { api } from "../api/client";
import { Pearl, greeting, useIdentity } from "../components/Pearl";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate } from "../router";
import { dateTime, duration, money, num, percent, tagType, timeAgo } from "../utils";

export function HomeView() {
  const identity = useIdentity();
  const status = useAsync(() => api.status(), []);
  const agent = useAsync(() => api.agent(), []);
  const projects = useAsync(() => api.projects(), []);
  const release = useAsync(() => api.release(), []);

  if (status.loading && !status.data) return <Loading label="Reading controller state" />;
  if (status.error && !status.data) return <LoadError message={status.error} onRetry={status.reload} />;

  const s = status.data!;
  const tasks = agent.data?.tasks ?? [];
  const running = tasks.filter((task) => ["running", "working", "started"].includes(String(task.state))).length;
  const recent = [...tasks].sort((a, b) => Number(b.updated ?? 0) - Number(a.updated ?? 0)).slice(0, 5);
  const session = s.session;

  return (
    <div className="nova-view">
      <div className="nova-hero">
        {identity.seed ? <Pearl seed={identity.seed} /> : null}
        <PageHeader
          kicker="NOVA · Mission control"
          title={greeting(identity.name)}
        description="One workspace for the controller, the coding agent, the GPU and the money. Nothing here is a mock."
        actions={
          <>
            <Button kind="primary" renderIcon={Chat} onClick={() => navigate("chat")}>
              New conversation
            </Button>
            <Button kind="tertiary" renderIcon={Add} onClick={() => navigate("agent/new")}>
              Give the agent a task
            </Button>
          </>
        }
        />
      </div>

      <section className="nova-stat-grid" aria-label="System at a glance">
        <Stat
          label="Controller"
          value={<Tag type={tagType(s.phase)}>{s.phase}</Tag>}
          detail={s.live_mode ? "Live actions enabled" : "Dry run — no paid starts"}
          tone={s.phase === "offline" ? "bad" : "ok"}
        />
        <Stat
          label="GPU session"
          value={session ? String(session.gpu || session.provider || "active") : "None"}
          detail={
            session
              ? `${duration(s.idle_shutdown_in_s)} to idle stop · ${money(session.price_per_hour ?? session.rate_usd_per_hour, 2)}/h`
              : s.provider.display
          }
          tone={session ? "ok" : "warn"}
        />
        <Stat
          label="Month spend"
          value={money(s.budget.total_usd)}
          detail={`${percent(s.budget.percent)} of ${money(s.budget.budget_usd, 0)} · ${money(s.budget.remaining_usd)} left`}
          tone={s.budget.percent > 85 ? "bad" : "accent"}
        />
        <Stat label="Agent tasks" value={num(tasks.length)} detail={`${running} running now`} tone={running ? "ok" : undefined} />
      </section>

      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={<a href="#/agent">All tasks</a>}>Agent work</SectionTitle>
          {agent.error ? <LoadError message={agent.error} onRetry={agent.reload} /> : null}
          {recent.length === 0 ? (
            <EmptyState
              title="No agent tasks yet"
              detail="Describe a change; the agent runs in an isolated workspace and reports files, tests and outcome."
              action={
                <Button size="sm" renderIcon={Play} onClick={() => navigate("agent/new")}>
                  Start a task
                </Button>
              }
            />
          ) : (
            <ul className="nova-list">
              {recent.map((task) => (
                <li key={task.id}>
                  <a href={`#/agent/${task.id}`} className="nova-list-row">
                    <span className="nova-list-main">
                      <strong>{task.prompt || `Task ${task.id}`}</strong>
                      <small>
                        {task.repo || "no repository"} · {timeAgo(task.updated)}
                      </small>
                    </span>
                    <Tag type={tagType(task.state)}>{task.state || "unknown"}</Tag>
                    {task.outcome ? <Tag type={tagType(task.outcome)}>{task.outcome}</Tag> : null}
                    <ArrowRight size={16} />
                  </a>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="nova-card">
          <SectionTitle hint={<a href="#/gpu">GPU</a>}>Inference</SectionTitle>
          <dl className="nova-kv">
            <div>
              <dt>Provider</dt>
              <dd>{s.provider.display}</dd>
            </div>
            <div>
              <dt>Requests in flight</dt>
              <dd>{s.inflight}</dd>
            </div>
            <div>
              <dt>Queued</dt>
              <dd>{s.queued}</dd>
            </div>
            <div>
              <dt>Idle policy</dt>
              <dd>{s.settings.idle_timeout_min ?? "—"} min</dd>
            </div>
            <div>
              <dt>Protected until</dt>
              <dd>{s.protected_until ? dateTime(s.protected_until) : "—"}</dd>
            </div>
            <div>
              <dt>Explain</dt>
              <dd>{s.explain || "—"}</dd>
            </div>
          </dl>
          <Button kind="tertiary" size="sm" renderIcon={Meter} onClick={() => navigate("gpu")}>
            Open GPU control
          </Button>
        </section>

        <section className="nova-card">
          <SectionTitle hint={<a href="#/project">Projects</a>}>Projects</SectionTitle>
          {projects.data?.projects.length ? (
            <ul className="nova-list compact">
              {projects.data.projects.slice(0, 5).map((project) => (
                <li key={project.repo}>
                  <a className="nova-list-row" href={`#/project/${encodeURIComponent(project.repo)}`}>
                    <span className="nova-list-main">
                      <strong>{project.name}</strong>
                      <small>
                        {project.tasks} tasks · {project.knowledge ?? 0} knowledge records
                      </small>
                    </span>
                    <ArrowRight size={16} />
                  </a>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No projects yet" detail="Projects appear after the first agent task or knowledge record." />
          )}
        </section>

        <section className="nova-card nova-span-2">
          <SectionTitle hint={<a href="#/costs">Costs</a>}>Money this month</SectionTitle>
          <div className="nova-money">
            <div>
              <span>Total</span>
              <strong>{money(s.budget.total_usd)}</strong>
            </div>
            <div>
              <span>Confirmed</span>
              <strong>{money(s.budget.confirmed_usd)}</strong>
            </div>
            <div>
              <span>Estimated</span>
              <strong>{money(s.budget.estimated_unconfirmed_usd)}</strong>
            </div>
            <div>
              <span>Remaining</span>
              <strong>{money(s.budget.remaining_usd)}</strong>
            </div>
          </div>
          <div className="nova-meter" aria-label={`${percent(s.budget.percent)} of budget used`}>
            <span style={{ width: `${Math.min(100, Math.max(0, s.budget.percent))}%` }} />
          </div>
          <div className="nova-row">
            <Button kind="ghost" size="sm" renderIcon={Money} onClick={() => navigate("costs")}>
              Cost ledger
            </Button>
            <Button kind="ghost" size="sm" renderIcon={Bot} onClick={() => navigate("models")}>
              Model routes
            </Button>
          </div>
        </section>

        <section className="nova-card">
          <SectionTitle hint={<a href="#/activity">Activity</a>}>Release</SectionTitle>
          {release.data ? (
            <dl className="nova-kv">
              <div>
                <dt>Installed</dt>
                <dd>{release.data.installed || "not reported"}</dd>
              </div>
              <div>
                <dt>Approved</dt>
                <dd>{release.data.approved || "no pending approval"}</dd>
              </div>
              <div>
                <dt>Branch</dt>
                <dd>
                  {release.data.repo} · {release.data.branch}
                </dd>
              </div>
              <div>
                <dt>Latest</dt>
                <dd>{release.data.latest?.message || release.data.error || "GitHub not checked"}</dd>
              </div>
            </dl>
          ) : release.loading ? (
            <Loading label="Checking release" />
          ) : (
            <LoadError message={release.error} onRetry={release.reload} />
          )}
        </section>
      </div>
    </div>
  );
}
