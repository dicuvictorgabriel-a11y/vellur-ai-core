import { useEffect, useMemo, useState } from "react";
import { Button, Tag } from "@carbon/react";
import { ArrowRight, Book } from "@carbon/icons-react";
import { api } from "../api/client";
import type { AgentTask, ProjectInfo } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { type Route } from "../router";
import { dateOnly, money, num, tagType, timeAgo } from "../utils";

export function ProjectView({ route }: { route: Route }) {
  const projects = useAsync(() => api.projects(), []);
  const agent = useAsync(() => api.agent(), []);
  const list = projects.data?.projects || [];
  const selectedRepo = route.param ? decodeURIComponent(route.param) : list[0]?.repo || "";
  const selected: ProjectInfo | undefined = list.find((project) => project.repo === selectedRepo);
  const knowledge = useAsync(
    () => (selectedRepo ? api.knowledge(selectedRepo) : Promise.resolve({ records: [] })),
    [selectedRepo],
  );
  const [openTask, setOpenTask] = useState<AgentTask | null>(null);

  useEffect(() => setOpenTask(null), [selectedRepo]);

  const tasks = useMemo(
    () => (agent.data?.tasks || []).filter((task) => !selectedRepo || task.repo === selectedRepo),
    [agent.data?.tasks, selectedRepo],
  );

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Projects"
        title="Repositories and work"
        description="Where the agent has been, what it cost, and the memory it left behind."
      />
      {projects.error ? <LoadError message={projects.error} onRetry={projects.reload} /> : null}
      {projects.loading && !projects.data ? <Loading label="Loading projects" /> : null}
      {!projects.loading && list.length === 0 ? (
        <EmptyState title="No projects yet" detail="Projects appear after an agent task or a knowledge record." />
      ) : (
        <div className="nova-split">
          <aside className="nova-card nova-project-list" aria-label="Projects">
            <SectionTitle hint={num(list.length)}>All projects</SectionTitle>
            <ul className="nova-list compact">
              {list.map((project) => (
                <li key={project.repo}>
                  <a className={`nova-list-row${selected?.repo === project.repo ? " is-active" : ""}`} href={`#/project/${encodeURIComponent(project.repo)}`}>
                    <span className="nova-list-main">
                      <strong>{project.name}</strong>
                      <small>{project.tasks} tasks · {timeAgo(project.updated)}</small>
                    </span>
                    <ArrowRight size={16} />
                  </a>
                </li>
              ))}
            </ul>
          </aside>
          <div className="nova-span-2 nova-stack">
            {selected ? (
              <>
                <section className="nova-stat-grid">
                  <Stat label="Tasks" value={num(selected.tasks)} detail="recorded runs" />
                  <Stat label="Knowledge" value={num(selected.knowledge)} detail="memory records" />
                  <Stat label="Cost" value={money(selected.cost?.confirmed_usd)} detail={`${money(selected.cost?.estimated_usd)} estimated`} />
                  <Stat label="Last activity" value={timeAgo(selected.updated)} detail={dateOnly(selected.updated)} />
                </section>
                <section className="nova-card">
                  <SectionTitle hint={<Tag type={tagType(selected.last?.state)}>{selected.last?.state || "no task"}</Tag>}>{selected.repo}</SectionTitle>
                  <p className="nova-note">Installed manifest: {projects.data?.installed || "not reported"}</p>
                  <ul className="nova-list">
                    {tasks.map((task) => (
                      <li key={task.id}>
                        <div className="nova-list-row">
                          <button type="button" className="nova-list-main nova-linkbutton" onClick={() => setOpenTask(openTask?.id === task.id ? null : task)}>
                            <strong>{task.prompt || `Task ${task.id}`}</strong>
                            <small>#{task.id} · {task.model || "model"} · {timeAgo(task.updated)}</small>
                          </button>
                          <Tag type={tagType(task.state)}>{task.state || "unknown"}</Tag>
                          {task.outcome ? <Tag type={tagType(task.outcome)}>{task.outcome}</Tag> : null}
                          <a href={`#/agent/${task.id}`} aria-label={`Open task ${task.id}`}><ArrowRight size={16} /></a>
                        </div>
                        {openTask?.id === task.id ? (
                          <div className="nova-task-inline">
                            <p>{String(task.title || task.prompt || "")}</p>
                            <dl className="nova-kv">
                              <div><dt>Cost</dt><dd>{money(task.cost_usd)}</dd></div>
                              <div><dt>Branch</dt><dd>{String(task.branch || "—")}</dd></div>
                              <div><dt>PR</dt><dd>{task.pr_url ? <a href={task.pr_url} target="_blank" rel="noreferrer">open</a> : "—"}</dd></div>
                              <div><dt>Error</dt><dd>{String(task.error || "—")}</dd></div>
                            </dl>
                          </div>
                        ) : null}
                      </li>
                    ))}
                    {tasks.length === 0 ? <li className="nova-note">No agent tasks for this project yet.</li> : null}
                  </ul>
                </section>
                <section className="nova-card">
                  <SectionTitle hint={`${knowledge.data?.records.length ?? 0} records`}>Memory</SectionTitle>
                  {knowledge.loading && !knowledge.data ? <Loading label="Loading knowledge" /> : null}
                  <ul className="nova-ledger">
                    {(knowledge.data?.records || []).slice(0, 8).map((record) => (
                      <li key={record.id}>
                        <span>
                          <strong>{record.title || record.key || `Record ${record.id}`}</strong>
                          <small>{record.namespace || "uncategorised"}</small>
                        </span>
                        <Tag type={tagType(record.status)}>{record.status || "record"}</Tag>
                      </li>
                    ))}
                  </ul>
                  <Button kind="ghost" renderIcon={Book} href={`#/library/${encodeURIComponent(selected.repo)}`}>Open project memory</Button>
                </section>
              </>
            ) : (
              <EmptyState title="Choose a project" />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
