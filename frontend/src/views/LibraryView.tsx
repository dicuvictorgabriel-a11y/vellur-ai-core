import { useMemo, useState } from "react";
import { Button, Modal, Select, Tag, TextArea, TextInput } from "@carbon/react";
import { Add, ArrowRight, Locked, Search, Unlocked } from "@carbon/icons-react";
import { api } from "../api/client";
import type { KnowledgeRecord } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle } from "../components/Ui";
import { useAsync } from "../hooks";
import { navigate, type Route } from "../router";
import { dateOnly, tagType } from "../utils";

type LibraryForm = {
  title: string;
  body: string;
  namespace: string;
  tags: string;
  paths: string;
  project: string;
};

const EMPTY_FORM: LibraryForm = { title: "", body: "", namespace: "Decisions", tags: "", paths: "", project: "" };

export function LibraryView({ route }: { route: Route }) {
  const projects = useAsync(() => api.projects(), []);
  const selected = route.param || projects.data?.projects[0]?.repo || "default";
  const knowledge = useAsync(() => api.knowledge(selected), [selected]);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<KnowledgeRecord | null>(null);
  const [editing, setEditing] = useState<KnowledgeRecord | null>(null);
  const [form, setForm] = useState<LibraryForm>(EMPTY_FORM);
  const [dialog, setDialog] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const records = useMemo(() => {
    const list = knowledge.data?.records || [];
    const q = query.trim().toLowerCase();
    return q ? list.filter((record) => `${record.title} ${record.body} ${record.tags} ${record.namespace}`.toLowerCase().includes(q)) : list;
  }, [knowledge.data?.records, query]);

  const startCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_FORM, project: selected !== "default" ? selected : "" });
    setError("");
    setDialog(true);
  };

  const startEdit = (record: KnowledgeRecord) => {
    setEditing(record);
    setForm({
      title: String(record.title || ""),
      body: String(record.body || ""),
      namespace: String(record.namespace || "Decisions"),
      tags: String(record.tags || ""),
      paths: String(record.paths || ""),
      project: String(record.project || ""),
    });
    setError("");
    setDialog(true);
  };

  const save = async () => {
    if (!form.title.trim() || !form.body.trim() || !form.namespace.trim()) {
      setError("Title, body and namespace are required.");
      return;
    }
    setBusy(true);
    setError("");
    const body = { ...form, project: form.project.trim() };
    try {
      if (editing) {
        await api.updateKnowledge(editing.id, { ...body, version: Number(editing.version || 0) });
      } else {
        await api.createKnowledge(body);
      }
      setDialog(false);
      knowledge.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the record.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (record: KnowledgeRecord) => {
    if (!window.confirm(`Delete “${record.title || record.key || record.id}”? This cannot be undone.`)) return;
    try {
      await api.deleteKnowledge(record.id);
      if (open?.id === record.id) setOpen(null);
      knowledge.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete the record.");
    }
  };

  const toggleLock = async (record: KnowledgeRecord) => {
    try {
      await api.lockKnowledge(record.id, !record.locked);
      knowledge.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not change the lock.");
    }
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Library"
        title="Project memory"
        description="Knowledge records serve the context bridge. Search, create, edit, lock and delete durable project memory here."
        actions={<Button kind="primary" renderIcon={Add} onClick={startCreate}>New record</Button>}
      />
      <div className="nova-split">
        <aside className="nova-card nova-project-list" aria-label="Projects">
          <SectionTitle hint={`${projects.data?.projects.length ?? 0}`}>Projects</SectionTitle>
          {projects.loading && !projects.data ? <Loading label="Loading projects" /> : null}
          <ul className="nova-list compact">
            {(projects.data?.projects || []).map((project) => (
              <li key={project.repo}>
                <a
                  href={`#/library/${encodeURIComponent(project.repo)}`}
                  className={`nova-list-row${selected === project.repo ? " is-active" : ""}`}
                >
                  <span className="nova-list-main">
                    <strong>{project.name}</strong>
                    <small>{project.knowledge ?? 0} records</small>
                  </span>
                  <ArrowRight size={16} />
                </a>
              </li>
            ))}
          </ul>
          {!projects.loading && !projects.data?.projects.length ? <EmptyState title="No projects yet" /> : null}
        </aside>

        <section className="nova-card nova-span-2">
          <SectionTitle
            hint={
              <span className="nova-inline-controls">
                <Search size={16} />
                <input
                  className="nova-filter"
                  placeholder="Search memory"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  aria-label="Search memory"
                />
              </span>
            }
          >
            {selected === "default" ? "Workspace memory" : selected}
          </SectionTitle>
          {knowledge.loading && !knowledge.data ? <Loading label="Loading memory" /> : null}
          {knowledge.error ? <LoadError message={knowledge.error} onRetry={knowledge.reload} /> : null}
          {error && !dialog ? <p className="nova-form-error" role="alert">{error}</p> : null}
          {!knowledge.loading && records.length === 0 ? <EmptyState title="No records match" /> : null}
          <ul className="nova-knowledge">
            {records.map((record) => (
              <li key={record.id}>
                <button type="button" className="nova-knowledge-head" onClick={() => setOpen(open?.id === record.id ? null : record)} aria-expanded={open?.id === record.id}>
                  <span>
                    <Tag type={tagType(record.status)}>{record.status || "record"}</Tag>
                    <strong>{record.title || record.key || `Record ${record.id}`}</strong>
                    {record.locked ? <Locked size={14} aria-label="Locked" /> : null}
                  </span>
                  <small>{record.namespace} · {dateOnly(record.updated)}</small>
                </button>
                {open?.id === record.id ? (
                  <div className="nova-knowledge-body">
                    <p>{record.body || "No body."}</p>
                    {record.tags ? <p className="nova-note">Tags: {String(record.tags)}</p> : null}
                    {record.paths ? <p className="nova-note">Paths: {String(record.paths)}</p> : null}
                    <div className="nova-row">
                      <Button size="sm" kind="tertiary" onClick={() => startEdit(record)}>Edit</Button>
                      <Button
                        size="sm"
                        kind="ghost"
                        renderIcon={record.locked ? Unlocked : Locked}
                        onClick={() => void toggleLock(record)}
                      >
                        {record.locked ? "Unlock" : "Lock"}
                      </Button>
                      <Button size="sm" kind="danger--ghost" disabled={Boolean(record.locked)} onClick={() => void remove(record)}>
                        Delete
                      </Button>
                    </div>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
          <Button kind="ghost" onClick={() => navigate("project")}>Open projects and tasks</Button>
        </section>
      </div>

      <Modal
        open={dialog}
        modalHeading={editing ? "Edit knowledge record" : "New knowledge record"}
        primaryButtonText={busy ? "Saving…" : "Save"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => setDialog(false)}
        onRequestSubmit={() => void save()}
      >
        <div className="nova-form-grid">
          <TextInput
            id="knowledge-title"
            labelText="Title"
            value={form.title}
            maxLength={200}
            onChange={(event) => setForm({ ...form, title: event.target.value })}
          />
          <Select
            id="knowledge-namespace"
            labelText="Namespace"
            value={form.namespace}
            onChange={(event) => setForm({ ...form, namespace: event.target.value })}
          >
            {(knowledge.data?.namespaces || [
              "Product", "Architecture", "Decisions", "Design System", "Constraints", "Operations", "Known Issues",
              "Implementation history", "Model Economics",
            ]).map((namespace) => <option key={namespace} value={namespace}>{namespace}</option>)}
          </Select>
          <TextArea
            id="knowledge-body"
            labelText="Body"
            value={form.body}
            rows={8}
            maxLength={20000}
            onChange={(event) => setForm({ ...form, body: event.target.value })}
          />
          <TextInput
            id="knowledge-tags"
            labelText="Tags (comma separated)"
            value={form.tags}
            maxLength={300}
            onChange={(event) => setForm({ ...form, tags: event.target.value })}
          />
          <TextInput
            id="knowledge-paths"
            labelText="Paths"
            value={form.paths}
            maxLength={1000}
            onChange={(event) => setForm({ ...form, paths: event.target.value })}
          />
          <TextInput
            id="knowledge-project"
            labelText="Project (owner/repository, blank = workspace)"
            value={form.project}
            maxLength={140}
            onChange={(event) => setForm({ ...form, project: event.target.value })}
          />
          {error ? <p className="nova-form-error" role="alert">{error}</p> : null}
        </div>
      </Modal>
    </div>
  );
}
