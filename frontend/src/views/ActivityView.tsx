import { useMemo, useState } from "react";
import { Button, Tag } from "@carbon/react";
import { Renew } from "@carbon/icons-react";
import { api } from "../api/client";
import type { TimelineEvent } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { dateTime, shortSha, tagType, timeAgo } from "../utils";

export function ActivityView() {
  const [category, setCategory] = useState("");
  const [query, setQuery] = useState("");
  const timeline = useAsync(() => api.timeline(`limit=200${category ? `&category=${encodeURIComponent(category)}` : ""}`), [category]);
  const release = useAsync(() => api.release(), []);
  const events = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (timeline.data?.events || []).filter((event) =>
      q ? `${event.title} ${event.detail || ""} ${event.category || ""}`.toLowerCase().includes(q) : true,
    );
  }, [timeline.data?.events, query]);
  const releaseData = release.data;

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Activity"
        title="Everything NOVA did"
        description="Requests, changes, checks, model choices, pull requests, merges and deployments in one timeline."
        actions={<Button kind="tertiary" renderIcon={Renew} onClick={() => { timeline.reload(); release.reload(); }}>Refresh</Button>}
      />
      <section className="nova-stat-grid">
        <Stat label="Events" value={String(timeline.data?.events.length ?? 0)} detail="most recent 200" />
        <Stat label="Synced" value={timeAgo(timeline.data?.synced_at)} detail="timeline journal" />
        <Stat
          label="Installed release"
          value={releaseData?.installed ? shortSha(releaseData.installed) : "unknown"}
          detail={releaseData?.installed || "installer has not reported"}
          tone={releaseData?.installed ? "ok" : "warn"}
        />
        <Stat
          label="Installer"
          value={releaseData?.installer_active ? "Running" : "Idle"}
          detail={releaseData?.approved ? `approved ${shortSha(releaseData.approved)}` : "no pending approval"}
          tone={releaseData?.installer_active ? "ok" : undefined}
        />
      </section>

      <section className="nova-card">
        <SectionTitle
          hint={
            <span className="nova-inline-controls">
              <input
                aria-label="Filter events"
                className="nova-filter"
                placeholder="Search activity"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
              <select aria-label="Category" value={category} onChange={(event) => setCategory(event.target.value)}>
                <option value="">All categories</option>
                {(timeline.data?.categories || []).map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </span>
          }
        >
          Timeline
        </SectionTitle>
        {timeline.loading && !timeline.data ? <Loading label="Loading timeline" /> : null}
        {timeline.error ? <LoadError message={timeline.error} onRetry={timeline.reload} /> : null}
        {!timeline.loading && events.length === 0 ? <EmptyState title="No events match" /> : null}
        <ol className="nova-timeline">
          {events.map((event) => (
            <Event key={event.event_id || event.id} event={event} />
          ))}
        </ol>
      </section>

      {releaseData ? (
        <section className="nova-card">
          <SectionTitle hint={`${releaseData.repo} · ${releaseData.branch}`}>Release</SectionTitle>
          <div className="nova-release">
            <div>
              <span>Approved</span>
              <strong>{shortSha(releaseData.approved)}</strong>
            </div>
            <div>
              <span>Installed</span>
              <strong>{shortSha(releaseData.installed)}</strong>
            </div>
            <div>
              <span>Previous</span>
              <strong>{shortSha(releaseData.previous)}</strong>
            </div>
            <div>
              <span>Latest on GitHub</span>
              <strong>{shortSha(releaseData.latest?.sha)}</strong>
            </div>
          </div>
          <dl className="nova-kv">
            <div>
              <dt>Request</dt>
              <dd>{releaseData.request || "—"}</dd>
            </div>
            <div>
              <dt>Latest message</dt>
              <dd>{releaseData.latest?.message || "—"}</dd>
            </div>
            <div>
              <dt>GitHub error</dt>
              <dd>{releaseData.error || "—"}</dd>
            </div>
          </dl>
          <p className="nova-note">
            Approval, install and rollback live in the Release view and the installer report; this page is read-only.
          </p>
        </section>
      ) : null}
    </div>
  );
}

function Event({ event }: { event: TimelineEvent }) {
  return (
    <li className="nova-timeline-item">
      <span className="nova-timeline-dot" aria-hidden="true" />
      <div className="nova-timeline-body">
        <div className="nova-timeline-head">
          <Tag type={tagType(event.category === "Release" ? "published" : event.category)}>{event.category || "Event"}</Tag>
          <strong>{event.title}</strong>
          <time>{dateTime(event.occurred_at)}</time>
        </div>
        {event.detail ? <p>{event.detail}</p> : null}
        <div className="nova-timeline-meta">
          {event.source ? <span>{event.source}</span> : null}
          {event.project ? <span>{event.project}</span> : null}
          {event.task_id ? <a href={`#/agent/${event.task_id}`}>task #{event.task_id}</a> : null}
          {event.conv_id ? <a href={`#/chat/${event.conv_id}`}>conversation #{event.conv_id}</a> : null}
        </div>
      </div>
    </li>
  );
}
