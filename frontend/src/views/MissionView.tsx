import { useState } from "react";
import { Button, Modal, Select, Tag, TextArea, TextInput } from "@carbon/react";
import { Add, Edit } from "@carbon/icons-react";
import { api } from "../api/client";
import type { Mission, MissionItem } from "../api/types";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { budgetMoney, missionTask, sceneList, selfDevRuns } from "../missionUtils";
import { dateOnly, errorMessage, money, num, tagType } from "../utils";

type MissionForm = {
  title: string;
  detail: string;
  state: string;
  plan: string;
  repo: string;
  base: string;
  blocked_reason: string;
  cost_estimate_usd: string;
  depends_on: string;
};

const EMPTY_ITEM: MissionForm = {
  title: "",
  detail: "",
  state: "backlog",
  plan: "",
  repo: "",
  base: "main",
  blocked_reason: "",
  cost_estimate_usd: "",
  depends_on: "",
};

export function MissionView() {
  const mission = useAsync(() => api.mission(), []);
  const [objectiveOpen, setObjectiveOpen] = useState(false);
  const [objectiveId, setObjectiveId] = useState<number | null>(null);
  const [objectiveTitle, setObjectiveTitle] = useState("");
  const [objectiveDetail, setObjectiveDetail] = useState("");
  const [objectiveCost, setObjectiveCost] = useState("");
  const [itemOpen, setItemOpen] = useState(false);
  const [editingItem, setEditingItem] = useState<MissionItem | null>(null);
  const [form, setForm] = useState<MissionForm>(EMPTY_ITEM);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const data = mission.data as (Mission & Record<string, any>) | null;
  const objective = data?.objective || null;
  const items = (data?.items || []) as Array<MissionItem & Record<string, any>>;
  const scenes = data ? sceneList(data) : [];
  const runs = data ? selfDevRuns(data) : [];
  const next = data?.next as Record<string, unknown> | null | undefined;
  const agent = data?.agent as Record<string, unknown> | undefined;
  const budget = data ? budgetMoney(data) : { spent: 0, estimate: 0 };

  if (mission.loading && !mission.data) return <Loading label="Reading the mission" />;
  if (mission.error) return <LoadError message={mission.error} onRetry={mission.reload} />;

  const openObjective = () => {
    setObjectiveId(null);
    setObjectiveTitle(String(objective?.title || ""));
    setObjectiveDetail(String(objective?.detail || ""));
    setObjectiveCost(objective?.cost_estimate_usd == null ? "" : String(objective.cost_estimate_usd));
    setError("");
    setObjectiveOpen(true);
  };

  const saveObjective = async () => {
    if (objectiveTitle.trim().length < 3) {
      setError("The objective title needs at least 3 characters.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      await api.saveObjective({
        id: objectiveId,
        title: objectiveTitle.trim(),
        detail: objectiveDetail.trim(),
        cost_estimate_usd: objectiveCost.trim() === "" ? null : Number(objectiveCost),
      });
      setObjectiveOpen(false);
      mission.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const park = async () => {
    if (!window.confirm("Park the active objective? Its items stay saved.")) return;
    try {
      await api.parkObjective();
      mission.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const openNewItem = () => {
    setEditingItem(null);
    setForm({ ...EMPTY_ITEM, repo: String(objective?.repo || "") });
    setError("");
    setItemOpen(true);
  };

  const openItem = (item: MissionItem) => {
    setEditingItem(item);
    setForm({
      title: item.title,
      detail: item.detail || "",
      state: String(item.state || "backlog"),
      plan: item.plan || "",
      repo: item.repo || "",
      base: item.base || "main",
      blocked_reason: item.blocked_reason || "",
      cost_estimate_usd: item.cost_estimate_usd == null ? "" : String(item.cost_estimate_usd),
      depends_on: item.depends_on == null ? "" : String(item.depends_on),
    });
    setError("");
    setItemOpen(true);
  };

  const saveItem = async () => {
    if (!form.title.trim()) {
      setError("An item needs a title.");
      return;
    }
    setBusy(true);
    setError("");
    const payload = {
      ...form,
      cost_estimate_usd: form.cost_estimate_usd.trim() === "" ? null : Number(form.cost_estimate_usd),
      depends_on: form.depends_on.trim() === "" ? null : Number(form.depends_on),
    };
    try {
      if (editingItem) {
        const { state, ...rest } = payload;
        await api.updateMissionItem(editingItem.id, { ...rest, state: ["idea", "backlog", "ready", "parked"].includes(state) ? state : undefined });
      } else {
        await api.createMissionItem(payload);
      }
      setItemOpen(false);
      mission.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const reactivate = (id: number, title: string, detail: string) => {
    setObjectiveId(id);
    setObjectiveTitle(title);
    setObjectiveDetail(detail || "");
    setObjectiveCost("");
    setError("");
    setObjectiveOpen(true);
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Mission"
        title="Mission Control"
        description="One active objective, its queue, the studio scenes and the self-development runs. Editing uses the controller's mission API."
        actions={
          <>
            <Button kind="ghost" onClick={openObjective}>{objective ? "Edit objective" : "Set objective"}</Button>
            <Button kind="primary" renderIcon={Add} onClick={openNewItem}>New item</Button>
          </>
        }
      />
      {error && !objectiveOpen && !itemOpen ? <p className="nova-form-error" role="alert">{error}</p> : null}
      <section className="nova-stat-grid">
        <Stat label="Objective" value={objective ? "Active" : "Not set"} detail={String(objective?.title || "The owner has not set one")} tone={objective ? "ok" : "warn"} />
        <Stat label="Items" value={num(items.length)} detail="planned work" />
        <Stat label="Spent on objective" value={money(budget.spent)} detail={`of ${money(budget.estimate)} estimate`} />
        <Stat label="Agent" value={agent?.available ? missionTask(agent) : "Unavailable"} detail={String(agent?.why || "runner state")} tone={agent?.available ? "ok" : "bad"} />
      </section>
      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={next ? `next: ${String(next.title || "")}` : "no next step"}>Objective</SectionTitle>
          {objective ? (
            <>
              <h3 className="nova-objective">{objective.title}</h3>
              {objective.detail ? <p>{objective.detail}</p> : null}
              <div className="nova-row">
                <Button size="sm" kind="ghost" renderIcon={Edit} onClick={openObjective}>Edit</Button>
                <Button size="sm" kind="danger--ghost" onClick={() => void park()}>Park objective</Button>
              </div>
            </>
          ) : (
            <EmptyState title="No active objective" detail="Set one, or reactivate a parked objective below." />
          )}
          {data?.parked_objectives?.length ? (
            <>
              <SectionTitle hint={`${data.parked_objectives.length}`}>Parked objectives</SectionTitle>
              <ul className="nova-list compact">
                {data.parked_objectives.map((parked) => (
                  <li key={parked.id}>
                    <div className="nova-list-row">
                      <span className="nova-list-main">
                        <strong>{parked.title}</strong>
                        <small>{dateOnly(Number(parked.updated) || 0)}</small>
                      </span>
                      <Button size="sm" kind="ghost" onClick={() => reactivate(parked.id, parked.title, String(parked.detail || ""))}>Reactivate</Button>
                    </div>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
        <section className="nova-card nova-span-2">
          <SectionTitle hint={`${items.length}`}>Queue</SectionTitle>
          {items.length ? (
            <ul className="nova-list">
              {items.map((item) => {
                const state = String(item.effective_state || item.state || "backlog");
                return (
                  <li key={item.id}>
                    <div className="nova-list-row">
                      <span className="nova-list-main">
                        <strong>{item.title}</strong>
                        <small>{item.detail || item.plan || "No detail yet"}</small>
                      </span>
                      <Tag type={tagType(state)}>{state}</Tag>
                      <Button size="sm" kind="ghost" hasIconOnly renderIcon={Edit} iconDescription={`Edit ${item.title}`} onClick={() => openItem(item)} />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <EmptyState title="The queue is empty" />
          )}
        </section>
        <section className="nova-card nova-span-2">
          <SectionTitle hint={`${scenes.length}`}>Studio scenes</SectionTitle>
          {scenes.length ? (
            <ul className="nova-ledger">
              {scenes.map((scene, index) => (
                <li key={String(scene.id ?? index)}>
                  <span>
                    <strong>{String(scene.title || "Scene")}</strong>
                    <small>{String(scene.description || "")}</small>
                  </span>
                  <Tag type="gray">v{String(scene.version ?? 1)}</Tag>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="No scenes yet" />
          )}
        </section>
        <section className="nova-card nova-span-2">
          <SectionTitle hint="self-development">Runs</SectionTitle>
          {runs.length ? (
            <div className="nova-table-wrap">
              <table className="nova-table">
                <thead><tr><th>Run</th><th>Goal</th><th>Status</th><th>Updated</th></tr></thead>
                <tbody>
                  {runs.map((run, index) => (
                    <tr key={String(run.id ?? index)}>
                      <td>{String(run.title || run.id || index + 1)}</td>
                      <td>{String(run.goal || "—")}</td>
                      <td><Tag type={tagType(String(run.status || run.state))}>{String(run.status || run.state || "unknown")}</Tag></td>
                      <td>{dateOnly(Number(run.updated || run.created) || 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="nova-note">No self-development runs recorded.</p>
          )}
        </section>
      </div>

      <Modal
        open={objectiveOpen}
        modalHeading={objective ? "Edit objective" : "Set objective"}
        primaryButtonText={busy ? "Saving…" : "Save objective"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => setObjectiveOpen(false)}
        onRequestSubmit={() => void saveObjective()}
      >
        <div className="nova-form-grid">
          <TextInput id="objective-title" labelText="Title" maxLength={200} value={objectiveTitle} onChange={(event) => setObjectiveTitle(event.target.value)} />
          <TextInput id="objective-cost" labelText="Estimated cost (USD, optional)" type="number" value={objectiveCost} onChange={(event) => setObjectiveCost(event.target.value)} />
        </div>
        <TextArea id="objective-detail" labelText="Detail" rows={5} maxLength={2000} value={objectiveDetail} onChange={(event) => setObjectiveDetail(event.target.value)} />
        {error ? <p className="nova-form-error" role="alert">{error}</p> : null}
      </Modal>

      <Modal
        open={itemOpen}
        modalHeading={editingItem ? "Edit mission item" : "New mission item"}
        primaryButtonText={busy ? "Saving…" : "Save item"}
        secondaryButtonText="Cancel"
        primaryButtonDisabled={busy}
        onRequestClose={() => setItemOpen(false)}
        onRequestSubmit={() => void saveItem()}
      >
        <div className="nova-form-grid">
          <TextInput id="item-title" labelText="Title" maxLength={200} value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} />
          <Select id="item-state" labelText="Queue state" value={form.state} onChange={(event) => setForm({ ...form, state: event.target.value })}>
            <option value="idea">Idea</option>
            <option value="backlog">Backlog</option>
            <option value="ready">Ready</option>
            <option value="parked">Parked</option>
          </Select>
          <TextInput id="item-repo" labelText="Repository (owner/name)" value={form.repo} onChange={(event) => setForm({ ...form, repo: event.target.value })} />
          <TextInput id="item-base" labelText="Base branch" value={form.base} onChange={(event) => setForm({ ...form, base: event.target.value })} />
          <TextInput id="item-cost" labelText="Cost estimate (USD)" type="number" value={form.cost_estimate_usd} onChange={(event) => setForm({ ...form, cost_estimate_usd: event.target.value })} />
          <TextInput id="item-depends" labelText="Depends on item id" type="number" value={form.depends_on} onChange={(event) => setForm({ ...form, depends_on: event.target.value })} />
        </div>
        <TextArea id="item-detail" labelText="Detail" rows={4} maxLength={4000} value={form.detail} onChange={(event) => setForm({ ...form, detail: event.target.value })} />
        <TextArea id="item-plan" labelText="Plan" rows={4} maxLength={8000} value={form.plan} onChange={(event) => setForm({ ...form, plan: event.target.value })} />
        <TextInput id="item-blocked" labelText="Blocked reason" maxLength={500} value={form.blocked_reason} onChange={(event) => setForm({ ...form, blocked_reason: event.target.value })} />
        {error ? <p className="nova-form-error" role="alert">{error}</p> : null}
      </Modal>
    </div>
  );
}
