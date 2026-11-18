import { useState } from "react";
import { Button, InlineNotification, Tag, TextArea } from "@carbon/react";
import { Renew } from "@carbon/icons-react";
import { api } from "../api/client";
import { EmptyState, LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { money, num, percent, tagType } from "../utils";

type FixedCost = { name?: string; usd?: number; amount_usd?: number; in_budget?: boolean; [key: string]: unknown };

export function FixedCostsEditor({
  initial,
  onSaved,
}: {
  initial: FixedCost[];
  onSaved: () => void;
}) {
  const toText = (list: FixedCost[]) =>
    list
      .map((item) => `${String(item.name || "Cost")} | ${Number(item.usd ?? item.amount_usd ?? 0)} | ${item.in_budget ? "yes" : "no"}`)
      .join("\n");
  const [text, setText] = useState(() => toText(initial));
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const save = async (event: React.FormEvent) => {
    event.preventDefault();
    setNotice(null);
    setSaving(true);
    try {
      const rows = text
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const parts = line.split("|").map((part) => part.trim());
          return { name: parts[0], usd: Number(parts[1] || 0), in_budget: /^y/i.test(parts[2] || "") };
        });
      await api.updateSettings({ fixed_costs: rows });
      setNotice({ kind: "success", text: "Fixed costs saved." });
      onSaved();
    } catch (reason) {
      setNotice({ kind: "error", text: String(reason) });
    } finally {
      setSaving(false);
    }
  };

  return (
    <form
      className="nova-fixed-costs"
      onSubmit={save}
      style={{ marginTop: "1rem" }}
    >
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      <TextArea
        id="nova-fixed-costs"
        labelText="Fixed monthly costs"
        rows={4}
        value={text}
        onChange={(event) => setText((event.target as HTMLTextAreaElement).value)}
        placeholder={"Domain | 12.00 | no\nRent | 0.25 | yes"}
      />
      <p className="nova-note">One per line: <code>name | usd | yes/no</code> — yes counts it inside the monthly budget.</p>
      <Button type="submit" size="sm" disabled={saving}>{saving ? "Saving…" : "Save fixed costs"}</Button>
    </form>
  );
}

export function CostsView() {
  const costs = useAsync(() => api.costs(), []);

  if (costs.loading && !costs.data) return <Loading label="Reading the cost ledger" />;
  if (costs.error) return <LoadError message={costs.error} onRetry={costs.reload} />;
  const data = costs.data!;
  const budget = data.budget;
  const days = data.by_day || [];
  const maxDay = Math.max(0.0001, ...days.map((day) => Number(day.cost_usd ?? 0)));
  const fixed = data.fixed_costs || [];
  const subscriptions = data.subscriptions || [];

  return (
    <div className="nova-view">
      <PageHeader
        kicker={`NOVA · Costs · ${data.month}`}
        title="Money and budgets"
        description="Confirmed means the provider reported it. Estimates are arithmetic, never presented as charges."
        actions={<Button kind="tertiary" renderIcon={Renew} onClick={costs.reload}>Refresh</Button>}
      />
      <section className="nova-stat-grid">
        <Stat label="In budget" value={money(budget.total_usd)} detail={`${percent(budget.percent)} of ${money(budget.budget_usd, 0)}`} tone={budget.percent > 85 ? "bad" : "accent"} />
        <Stat label="Confirmed" value={money(budget.confirmed_usd)} detail="provider-reported" tone="ok" />
        <Stat label="Estimated" value={money(budget.estimated_unconfirmed_usd)} detail="not a charge yet" tone="warn" />
        <Stat label="Remaining" value={money(budget.remaining_usd)} detail={budget.exhausted ? "budget exhausted" : "before paid calls stop"} tone={budget.exhausted ? "bad" : "ok"} />
      </section>
      <div className="nova-meter is-large" aria-label={`${percent(budget.percent)} of budget used`}>
        <span style={{ width: `${Math.min(100, Math.max(0, budget.percent))}%` }} />
      </div>
      {budget.note ? <p className="nova-note">{budget.note}</p> : null}

      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={`${days.length} days with requests`}>Daily spend</SectionTitle>
          {days.length === 0 ? (
            <EmptyState title="No requests this month" detail="Usage appears here after inference is routed through NOVA." />
          ) : (
            <div className="nova-bars" role="img" aria-label="Daily spend chart">
              {days.map((day) => (
                <div key={day.day} className="nova-bar">
                  <span
                    className="nova-bar-fill"
                    style={{ height: `${Math.max(2, (Number(day.cost_usd) / maxDay) * 100)}%` }}
                    title={`${day.day}: ${money(day.cost_usd, 4)} · ${day.requests} requests`}
                  />
                  <small>{day.day.slice(5)}</small>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="nova-card">
          <SectionTitle hint="outside the monthly inference budget">Fixed costs</SectionTitle>
          {fixed.length === 0 && subscriptions.length === 0 ? (
            <EmptyState title="None recorded" />
          ) : (
            <ul className="nova-ledger">
              {[...fixed, ...subscriptions].map((item, index) => (
                <li key={`${item.name || "cost"}-${index}`}>
                  <span>{String(item.name || item.label || "Cost")}</span>
                  <strong>{money(Number(item.usd ?? item.amount_usd ?? 0))}</strong>
                </li>
              ))}
            </ul>
          )}
          <p className="nova-note">All-in total: {money(budget.all_in_total_usd)}. Fixed outside budget: {money(budget.fixed_outside_budget_usd)}.</p>
          <FixedCostsEditor initial={fixed} onSaved={() => costs.reload()} />
        </section>

        <section className="nova-card nova-span-2">
          <SectionTitle hint={`${data.by_target?.length ?? 0} targets`}>By target</SectionTitle>
          <div className="nova-table-wrap">
            <table className="nova-table">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Requests</th>
                  <th>Tokens in / out</th>
                  <th>Cost</th>
                  <th>Source</th>
                  <th>Avg latency</th>
                </tr>
              </thead>
              <tbody>
                {(data.by_target || []).map((row) => (
                  <tr key={row.target}>
                    <td><code>{row.target}</code></td>
                    <td>{num(row.requests)}</td>
                    <td>{num(row.in_tokens)} / {num(row.out_tokens)}</td>
                    <td>{money(row.cost_usd, 4)}</td>
                    <td><Tag type={row.cost_source === "provider-reported" ? "green" : "gray"}>{row.cost_source || "unknown"}</Tag></td>
                    <td>{num(row.avg_ms)} ms</td>
                  </tr>
                ))}
                {(data.by_target || []).length === 0 ? (
                  <tr><td colSpan={6}>No requests this month.</td></tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>

        <section className="nova-card">
          <SectionTitle hint="what the money is for">Allocation</SectionTitle>
          {(data.by_use || []).length ? (
            <ul className="nova-ledger">
              {data.by_use.map((row) => (
                <li key={row.name}>
                  <span>{row.name}</span>
                  <strong>{money(row.usd)}</strong>
                  <Tag type={row.in_budget ? "blue" : tagType("neutral")}>{row.in_budget ? "budget" : "outside"}</Tag>
                </li>
              ))}
            </ul>
          ) : (
            <p className="nova-note">No non-inference costs recorded.</p>
          )}
        </section>
      </div>
    </div>
  );
}
