import { useEffect, useState } from "react";
import { Button, InlineNotification, Modal, NumberInput, Tag } from "@carbon/react";
import { api } from "../../api/client";
import { timeAgo } from "../../utils";
import type { RoutingDecision, RoutingState } from "../../api/types";

export function RoutingPolicy({
  data,
  onSaved,
}: {
  data: RoutingState;
  onSaved: () => void;
}) {
  const [mode, setMode] = useState(data.mode || "balanced");
  const [auto, setAuto] = useState<number>(Number(data.allowance_usd ?? 0));
  const [task, setTask] = useState<number>(Number(data.task_limit_usd ?? 0));
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [decision, setDecision] = useState<RoutingDecision | null>(null);
  const [decisionId, setDecisionId] = useState<number | null>(null);

  useEffect(() => {
    setMode(data.mode || "balanced");
    setAuto(Number(data.allowance_usd ?? 0));
    setTask(Number(data.task_limit_usd ?? 0));
  }, [data]);

  const save = async (changes: Record<string, unknown>) => {
    setSaving(true);
    setNotice(null);
    try {
      await api.saveRouting(changes);
      setNotice({ kind: "success", text: "Routing policy saved." });
      onSaved();
    } catch (reason) {
      setNotice({ kind: "error", text: String(reason) });
    } finally {
      setSaving(false);
    }
  };

  const openDecision = (id: number) => {
    setDecisionId(id);
    api
      .routingDecision(String(id))
      .then(setDecision)
      .catch((reason) => setNotice({ kind: "error", text: String(reason) }));
  };

  const decisions = data.decisions || [];

  return (
    <section className="nova-card" aria-label="Routing policy">
      <header className="nova-card-head">
        <h2>Routing policy</h2>
        <p className="nova-note">
          How the model router picks targets. Economy prefers the cheapest call that is likely to succeed; quality
          prefers the best observed success; balanced trades cost against success; auto picks a tier per request type
          and can spend the allowance you set; manual always uses the saved route order and never spends on its own.
        </p>
      </header>
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      <p className="nova-note" aria-live="polite">
        {data.active
          ? <strong>Automatic routing is on.</strong>
          : mode === "manual"
            ? <strong>Manual routing.</strong>
            : <><strong>Waiting for an allowance:</strong> saved routes are used, the router does not spend on its own.</>}
      </p>
      <div className="nova-seg" role="radiogroup" aria-label="Routing mode">
        {(data.modes || ["auto", "economy", "balanced", "quality", "manual"]).map((value) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={mode === value}
            className={mode === value ? "is-on" : ""}
            onClick={() => {
              setMode(value);
              save({ routing_mode: value });
            }}
          >
            {value}
          </button>
        ))}
      </div>
      {mode === "auto" ? (
        <form
          className="nova-policy-form"
          onSubmit={(event) => {
            event.preventDefault();
            save({ auto_allowance_usd: auto, auto_task_limit_usd: task });
          }}
        >
          <NumberInput
            id="nova-auto-allowance"
            label="Auto allowance per month (USD)"
            value={auto}
            onChange={(event) => setAuto(Number((event.target as HTMLInputElement).value))}
          />
          <NumberInput
            id="nova-auto-task"
            label="Auto spend cap per task (USD)"
            value={task}
            onChange={(event) => setTask(Number((event.target as HTMLInputElement).value))}
          />
          <Button type="submit" kind="primary" disabled={saving}>
            {saving ? "Saving…" : "Save auto budget"}
          </Button>
        </form>
      ) : null}
      {typeof data.spent_month_usd === "number" && data.pool?.length ? (
        <p className="nova-note">
          Auto spent {data.spent_month_usd.toFixed(4)} USD this month; {data.reserved_usd ?? 0} USD reserved from the
          pool of {data.pool.length} targets.
        </p>
      ) : null}
      <h3 className="nova-subhead">Decisions</h3>
      {decisions.length ? (
        <ul className="nova-decisions">
          {decisions.slice(0, 10).map((entry) => (
            <li key={entry.id}>
              <span className="nova-decision-line">
                <code>{entry.chosen}</code>
                {entry.outcome ? (
                  <Tag size="sm" type={entry.outcome === "ok" ? "green" : "red"}>
                    {entry.outcome}
                  </Tag>
                ) : null}
                {typeof entry.est_usd === "number" ? <span>{entry.est_usd} USD</span> : null}
                <time>{timeAgo(entry.ts)}</time>
              </span>
              <Button kind="ghost" size="sm" onClick={() => openDecision(entry.id)}>
                Why?
              </Button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="nova-note">No routing decisions recorded yet.</p>
      )}
      <Modal
        open={Boolean(decision) || Boolean(decisionId)}
        modalHeading={`Routing decision ${decisionId || ""}`}
        passiveModal
        onRequestClose={() => {
          setDecision(null);
          setDecisionId(null);
        }}
      >
        {decision ? (
          <div className="nova-decision-detail">
            <p>
              Chosen <code>{decision.chosen}</code>.
            </p>
            {decision.reason ? <p>{decision.reason}</p> : null}
            {decision.required ? (
              <p>
                Required: <code>{decision.required}</code>
              </p>
            ) : null}
            {decision.ranked?.length ? (
              <table className="nova-table">
                <thead>
                  <tr>
                    <th>Target</th>
                    <th>Usable</th>
                    <th>Why</th>
                  </tr>
                </thead>
                <tbody>
                  {decision.ranked.map((row) => (
                    <tr key={row.target}>
                      <td>
                        <code>{row.target}</code>
                      </td>
                      <td>{row.usable ? "yes" : "no"}</td>
                      <td>{row.why}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : null}
          </div>
        ) : (
          <p>Loading decision…</p>
        )}
      </Modal>
    </section>
  );
}
