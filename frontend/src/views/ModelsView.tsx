import { useEffect, useState } from "react";
import { Button, InlineNotification, Tag, TextInput } from "@carbon/react";
import { Checkmark, Warning } from "@carbon/icons-react";
import { api } from "../api/client";
import type { LlmTarget } from "../api/types";
import { LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { errorMessage } from "../utils";

export function ModelsView() {
  const llm = useAsync(() => api.llm(), []);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    if (llm.data) {
      setDraft(
        Object.fromEntries(
          Object.entries(llm.data.routes || {}).map(([role, targets]) => [role, (targets || []).join(", ")]),
        ),
      );
    }
  }, [llm.data]);

  if (llm.loading && !llm.data) return <Loading label="Reading model routes" />;
  if (llm.error) return <LoadError message={llm.error} onRetry={llm.reload} />;
  const data = llm.data!;
  const providers = data.providers || [];
  const configured = providers.filter((provider) => provider.enabled !== false && provider.key_set !== false).length;

  const save = async () => {
    setSaving(true);
    setNotice("");
    try {
      const routes = Object.fromEntries(
        Object.entries(draft).map(([role, value]) => [
          role,
          value
            .split(",")
            .map((target) => target.trim())
            .filter(Boolean),
        ]),
      );
      await api.updateRoutes(routes);
      setNotice("Routes saved. New messages use the updated order.");
      llm.reload();
    } catch (reason) {
      setNotice(errorMessage(reason));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Models"
        title="Router and providers"
        description="Public NOVA names map to ordered targets. The first usable target answers; paid fallback is explicit."
        actions={
          <Button kind="primary" onClick={() => void save()} disabled={saving}>
            {saving ? "Saving…" : "Save routes"}
          </Button>
        }
      />
      <section className="nova-stat-grid">
        <Stat label="Providers" value={String(providers.length)} detail={`${configured} with credentials`} />
        <Stat
          label="Budget"
          value={data.budget_exhausted ? "Exhausted" : "Available"}
          detail="paid targets respect the monthly cap"
          tone={data.budget_exhausted ? "bad" : "ok"}
        />
        <Stat label="Paid fallback" value={data.paid_fallback ? "On" : "Off"} detail="explicitly configured by the owner" />
        <Stat label="Image role" value={(data.routes?.["vellur-image"] || []).join(", ") || "unset"} detail="vellur-image needs an image-output model" />
      </section>
      {notice ? (
        <InlineNotification
          kind={notice.startsWith("Routes saved") ? "success" : "error"}
          lowContrast
          title={notice.startsWith("Routes saved") ? "Saved" : "Not saved"}
          subtitle={notice}
          onCloseButtonClick={() => setNotice("")}
        />
      ) : null}
      <section className="nova-card">
        <SectionTitle hint="comma-separated provider:model, first usable wins">Routes</SectionTitle>
        <div className="nova-route-grid">
          {Object.entries(data.roles || {}).map(([role, label]) => (
            <div className="nova-route" key={role}>
              <div className="nova-route-head">
                <div>
                  <strong>{role}</strong>
                  <small>{label}</small>
                </div>
                <Tag type={draft[role]?.trim() ? "green" : "red"}>{draft[role]?.trim() ? "configured" : "empty"}</Tag>
              </div>
              <TextInput
                id={`route-${role}`}
                labelText={`${role} targets`}
                hideLabel
                value={draft[role] ?? ""}
                onChange={(event) => setDraft((current) => ({ ...current, [role]: event.target.value }))}
              />
              <div className="nova-targets">
                {(data.effective?.[role] || []).map((target: LlmTarget) => (
                  <div key={target.target} className={`nova-target${target.usable ? "" : " is-unusable"}`}>
                    {target.usable ? <Checkmark size={14} /> : <Warning size={14} />}
                    <code>{target.target}</code>
                    <span>
                      {target.usable ? `${target.price?.[0] ?? 0} / ${target.price?.[1] ?? 0} per Mtok` : target.why || target.code || "unusable"}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="nova-note">The classic dashboard also exposes catalog refresh, provider keys and a test call.</p>
      </section>
      <section className="nova-card">
        <SectionTitle hint={`${providers.length} providers`}>Providers</SectionTitle>
        <div className="nova-table-wrap">
          <table className="nova-table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Plan</th>
                <th>Credentials</th>
                <th>Capabilities</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {providers.map((provider) => (
                <tr key={provider.name}>
                  <td>
                    <strong>{provider.display || provider.name}</strong>
                    <br />
                    <small>{String(provider.base_url || "")}</small>
                  </td>
                  <td>{String(provider.plan || "—")}</td>
                  <td>
                    <Tag type={provider.key_set ? "green" : "gray"}>{provider.key_set ? "set" : "missing"}</Tag>
                  </td>
                  <td>{(provider.capability_labels as string[] | undefined)?.join(", ") || "—"}</td>
                  <td>{String(provider.note || "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
