import { useState } from "react";
import { Button, InlineNotification, Tag, TextInput } from "@carbon/react";
import { api } from "../../api/client";
import type { ModelsCatalogRow } from "../../api/types";

type Group = "image" | "vision" | "chat" | "coding" | "other";

function groupOf(row: ModelsCatalogRow): Group {
  if (row.image_generation) return "image";
  if (row.vision) return "vision";
  if (row.coding) return "coding";
  if (!row.labels?.length) return "chat";
  return "other";
}

const GROUPS: Array<{ key: Group; title: string; note: string }> = [
  { key: "image", title: "Image generation", note: "Models that create images for chat and agent tasks." },
  { key: "vision", title: "Vision", note: "Models that can read images." },
  { key: "coding", title: "Coding", note: "Models that follow code tool calls well." },
  { key: "chat", title: "Chat", note: "Everyday conversation models." },
  { key: "other", title: "Other", note: "Discovered models that declare no strong capability." },
];

function money(value: number | undefined, perMillion: boolean): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (!perMillion) return `${value.toFixed(4)} USD`;
  if (value === 0) return "free";
  return `${value} USD / Mtok`;
}

export function Catalog({
  rows,
  providers,
  onChanged,
}: {
  rows: ModelsCatalogRow[];
  providers: string[];
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = () => {
    setBusy(true);
    setNotice(null);
    void (async () => {
      try {
        for (const provider of providers) {
          await api.loadCatalog(provider);
        }
        setNotice({ kind: "success", text: "Catalog refreshed." });
        onChanged();
      } catch (reason) {
        setNotice({ kind: "error", text: String(reason) });
      } finally {
        setBusy(false);
      }
    })();
  };

  const useForImages = (target: string) => {
    setBusy(true);
    setNotice(null);
    void api
      .updateRoutes({ "vellur-image": [target] })
      .then(() => {
        setNotice({ kind: "success", text: `${target} is now the image generation target.` });
        return onChanged();
      })
      .catch((reason) => setNotice({ kind: "error", text: String(reason) }))
      .finally(() => setBusy(false));
  };

  const filtered = rows.filter((row) => {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;
    return [row.target, row.maker, row.model, ...(row.labels || [])].some((field) =>
      String(field || "").toLowerCase().includes(needle),
    );
  });

  return (
    <section className="nova-card" aria-label="Models and capabilities">
      <header className="nova-card-head">
        <h2>Models and capabilities</h2>
        <p className="nova-note">
          Discovered models are grouped by what they can actually do. Vision means the model can read images; image
          generation means it can create them. A model is never used for a task it does not declare.
        </p>
      </header>
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      <div className="nova-catalog-tools">
        <TextInput
          id="nova-catalog-filter"
          labelText="Filter models"
          hideLabel
          placeholder="Search model or maker…"
          value={query}
          onChange={(event) => setQuery((event.target as HTMLInputElement).value)}
        />
        <Button size="sm" onClick={refresh} disabled={busy}>
          {busy ? "Loading…" : "Refresh catalog"}
        </Button>
      </div>
      {GROUPS.map((group) => {
        const list = filtered.filter((row) => groupOf(row) === group.key);
        if (!list.length) return null;
        return (
          <div key={group.key} className="nova-catalog-group">
            <h3 className="nova-subhead">{group.title}</h3>
            <p className="nova-note">{group.note}</p>
            <table className="nova-table">
              <thead>
                <tr>
                  <th>Target</th>
                  <th>Maker</th>
                  <th>Context</th>
                  <th>Price in/out</th>
                  <th>Labels</th>
                  <th>{group.key === "image" ? "Set as image target" : ""}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((row) => (
                  <tr key={row.target}>
                    <td>
                      <code>{row.target}</code>
                      {!row.usable ? (
                        <div>
                          <Tag size="sm" type="gray">
                            idle
                          </Tag>
                        </div>
                      ) : null}
                    </td>
                    <td>{row.maker || "—"}</td>
                    <td>{row.ctx ? `${Math.round(row.ctx / 1000)}k` : "—"}</td>
                    <td>{group.key === "image" ? money(row.image_price, false) : `${money(row.in, true)} / ${money(row.out, true)}`}</td>
                    <td>{row.labels?.length ? row.labels.join(", ") : "—"}</td>
                    <td>
                      {group.key === "image" && row.usable ? (
                        <Button size="sm" onClick={() => useForImages(row.target)} disabled={busy}>
                          Use for images
                        </Button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      })}
      {!filtered.length ? <p className="nova-note">No models match.</p> : null}
    </section>
  );
}
