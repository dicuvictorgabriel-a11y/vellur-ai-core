import { useMemo, useState } from "react";
import { Button, InlineNotification, Select, SelectItem, Tag } from "@carbon/react";
import { ArrowUp, ArrowDown, Close, Add } from "@carbon/icons-react";
import type { LlmTarget } from "../../api/types";

const ROLE_HINTS: Record<string, string> = {
  "vellur-coder": "Coding model used for Build/Ship work and the agent.",
  "vellur-chat": "Everyday conversation model.",
  "vellur-max": "Long-context and heavy reasoning model.",
  "vellur-image": "Image generation model for chat and agent tasks.",
};

function Chip({
  target,
  index,
  total,
  usable,
  why,
  onMove,
  onRemove,
}: {
  target: string;
  index: number;
  total: number;
  usable?: boolean;
  why?: string;
  onMove: (delta: number) => void;
  onRemove: () => void;
}) {
  return (
    <li className="nova-route-chip">
      <span className="nova-route-chip-name">
        {target}
        {usable === undefined ? null : (
          <Tag size="sm" type={usable ? "green" : "gray"}>
            {usable ? "usable" : "idle"}
          </Tag>
        )}
      </span>
      {why ? <small className="nova-route-chip-why">{why}</small> : null}
      <span className="nova-route-chip-actions">
        <Button
          kind="ghost"
          size="sm"
          hasIconOnly
          iconDescription="Move up"
          renderIcon={ArrowUp}
          disabled={index === 0}
          onClick={() => onMove(-1)}
        />
        <Button
          kind="ghost"
          size="sm"
          hasIconOnly
          iconDescription="Move down"
          renderIcon={ArrowDown}
          disabled={index === total - 1}
          onClick={() => onMove(1)}
        />
        <Button
          kind="ghost"
          size="sm"
          hasIconOnly
          iconDescription={`Remove ${target}`}
          renderIcon={Close}
          disabled={total <= 1}
          onClick={onRemove}
        />
      </span>
    </li>
  );
}

export function RouteEditor({
  routes,
  effective,
  pool,
  saving,
  onSave,
}: {
  routes: Record<string, string[]>;
  effective: Record<string, LlmTarget[]>;
  pool: string[];
  saving: boolean;
  onSave: (routes: Record<string, string[]>) => Promise<void>;
}) {
  const [draft, setDraft] = useState<Record<string, string[]>>(() =>
    Object.fromEntries(Object.entries(routes).map(([role, targets]) => [role, [...(targets || [])]])),
  );
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [adding, setAdding] = useState<Record<string, string>>({});

  const effectiveMap = useMemo(() => {
    const map: Record<string, Record<string, LlmTarget>> = {};
    for (const [role, targets] of Object.entries(effective || {})) {
      map[role] = Object.fromEntries((targets || []).map((entry) => [entry.target, entry]));
    }
    return map;
  }, [effective]);

  const dirty = useMemo(() => {
    const keys = new Set([...Object.keys(draft), ...Object.keys(routes)]);
    for (const key of keys) {
      const left = draft[key] || [];
      const right = routes[key] || [];
      if (left.length !== right.length || left.some((target, index) => target !== right[index])) return true;
    }
    return false;
  }, [draft, routes]);

  const mutate = (role: string, next: string[]) => setDraft((prev) => ({ ...prev, [role]: next }));

  const move = (role: string, index: number, delta: number) => {
    const list = [...(draft[role] || [])];
    const swap = index + delta;
    if (swap < 0 || swap >= list.length) return;
    [list[index], list[swap]] = [list[swap], list[index]];
    mutate(role, list);
  };

  const add = (role: string) => {
    const target = (adding[role] || "").trim();
    if (!target) return;
    const list = draft[role] || [];
    if (list.includes(target)) return;
    mutate(role, [...list, target]);
    setAdding((prev) => ({ ...prev, [role]: "" }));
  };

  return (
    <section className="nova-card" aria-label="Route chains">
      <header className="nova-card-head">
        <h2>Route chains</h2>
        <p className="nova-note">
          Public names map to ordered targets. The first usable target answers; the rest are fallback. Drag-free
          editing: order with the arrows, remove, add from the pool.
        </p>
      </header>
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      <form
        className="nova-route-editor"
        onSubmit={(event) => {
          event.preventDefault();
          setNotice(null);
          onSave(draft)
            .then(() => setNotice({ kind: "success", text: "Routes saved. New messages use the updated order." }))
            .catch((reason) => setNotice({ kind: "error", text: String(reason) }));
        }}
      >
        {Object.entries(draft).map(([role, targets]) => (
          <fieldset key={role} className="nova-route-role">
            <legend>{role}</legend>
            {ROLE_HINTS[role] ? <p className="nova-note">{ROLE_HINTS[role]}</p> : null}
            <ul className="nova-route-list">
              {(targets || []).map((target, index) => {
                const info = effectiveMap[role]?.[target];
                return (
                  <Chip
                    key={`${role}-${target}-${index}`}
                    target={target}
                    index={index}
                    total={(targets || []).length}
                    usable={info ? info.usable : undefined}
                    why={info ? info.why : undefined}
                    onMove={(delta) => move(role, index, delta)}
                    onRemove={() => mutate(role, (targets || []).filter((_, at) => at !== index))}
                  />
                );
              })}
            </ul>
            <div className="nova-route-add">
              <Select
                id={`nova-route-add-${role}`}
                size="sm"
                labelText={`Add target to ${role}`}
                hideLabel
                value={adding[role] || ""}
                onChange={(event) => setAdding((prev) => ({ ...prev, [role]: (event.target as HTMLSelectElement).value }))}
              >
                <SelectItem value="" text="Add target…" />
                {pool
                  .filter((target) => !(targets || []).includes(target))
                  .map((target) => (
                    <SelectItem key={target} value={target} text={target} />
                  ))}
              </Select>
              <Button
                kind="ghost"
                size="sm"
                hasIconOnly
                iconDescription={`Add to ${role}`}
                renderIcon={Add}
                onClick={() => add(role)}
              />
            </div>
          </fieldset>
        ))}
        <div className="nova-card-actions">
          <Button type="submit" kind="primary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save routing"}
          </Button>
          <Button kind="secondary" disabled={saving || !dirty} onClick={() => setDraft(Object.fromEntries(Object.entries(routes).map(([role, targets]) => [role, [...(targets || [])]])))}>
            Discard
          </Button>
        </div>
      </form>
    </section>
  );
}
