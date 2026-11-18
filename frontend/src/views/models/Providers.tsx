import { useState } from "react";
import { Button, InlineNotification, PasswordInput, Select, SelectItem, TextInput } from "@carbon/react";
import { api } from "../../api/client";
import type { LlmState } from "../../api/types";

interface ProviderRow {
  name: string;
  key_set?: boolean;
  base_url?: string;
  capability_labels?: string[];
  note?: string;
}

function toRows(data: LlmState): ProviderRow[] {
  const list = Array.isArray(data.providers) ? (data.providers as ProviderRow[]) : [];
  return list;
}

export function Providers({
  data,
  onChanged,
}: {
  data: LlmState;
  onChanged: () => void;
}) {
  const rows = toRows(data);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [keys, setKeys] = useState<Record<string, string>>({});
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [newName, setNewName] = useState("");
  const [newBase, setNewBase] = useState("");
  const [testTarget, setTestTarget] = useState(data.routes?.["vellur-coder"]?.[0] || "");

  const run = async (label: string, job: () => Promise<void>, reload: boolean) => {
    setBusy(label);
    setNotice(null);
    try {
      await job();
      setNotice({ kind: "success", text: `${label} done.` });
      if (reload) onChanged();
    } catch (reason) {
      setNotice({ kind: "error", text: `${label} failed: ${String(reason)}` });
    } finally {
      setBusy(null);
    }
  };

  const allTargets = Array.from(
    new Set([...Object.values(data.routes || {}).flat(), ...Object.keys(data.effective || {})]),
  );

  return (
    <section className="nova-card" aria-label="Providers and keys">
      <header className="nova-card-head">
        <h2>Providers and keys</h2>
        <p className="nova-note">
          Keys are stored write-only on the server and are never shown again. Loading the catalog fetches the models a
          provider publishes; the models view groups them by what they can do.
        </p>
      </header>
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      {rows.length ? (
        <table className="nova-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Key</th>
              <th>Base URL</th>
              <th>Catalog</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((provider) => (
              <tr key={provider.name}>
                <td>
                  <code>{provider.name}</code>
                  {provider.capability_labels?.length ? <div className="nova-note">{provider.capability_labels.join(", ")}</div> : null}
                </td>
                <td>
                  <PasswordInput
                    id={`nova-key-${provider.name}`}
                    labelText={`API key for ${provider.name}`}
                    hideLabel
                    placeholder={provider.key_set ? "•••• stored — paste to replace" : "Paste API key"}
                    value={keys[provider.name] || ""}
                    onChange={(event) => setKeys((prev) => ({ ...prev, [provider.name]: (event.target as HTMLInputElement).value }))}
                  />
                </td>
                <td>
                  <TextInput
                    id={`nova-url-${provider.name}`}
                    labelText="Base URL"
                    hideLabel
                    placeholder={provider.base_url || "https://…/v1"}
                    value={urls[provider.name] || ""}
                    onChange={(event) => setUrls((prev) => ({ ...prev, [provider.name]: (event.target as HTMLInputElement).value }))}
                  />
                </td>
                <td>
                  <Button
                    size="sm"
                    onClick={() =>
                      run(`Catalog ${provider.name}`, async () => {
                        await api.loadCatalog(provider.name);
                        const list = await api.catalog(provider.name);
                        setNotice({ kind: "success", text: `${provider.name}: ${list.length} models in catalog.` });
                      }, true)
                    }
                    disabled={busy !== null}
                  >
                    Load catalog
                  </Button>
                </td>
                <td className="nova-row-actions">
                  <Button
                    size="sm"
                    onClick={() =>
                      run(`Key ${provider.name}`, async () => {
                        await api.saveProvider(provider.name, {
                          api_key: keys[provider.name] || undefined,
                          base_url: urls[provider.name] || undefined,
                        });
                        setKeys((prev) => ({ ...prev, [provider.name]: "" }));
                      }, true)
                    }
                    disabled={busy !== null || (!keys[provider.name] && !urls[provider.name])}
                  >
                    Save
                  </Button>
                  {provider.key_set ? (
                    <Button
                      kind="danger--ghost"
                      size="sm"
                      onClick={() =>
                        run(`Remove key ${provider.name}`, async () => {
                          if (!window.confirm(`Remove the key for ${provider.name}? Calls to it will fail.`)) return;
                          await api.deleteProvider(provider.name);
                        }, true)
                      }
                      disabled={busy !== null}
                    >
                      Remove key
                    </Button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="nova-note">No providers configured yet.</p>
      )}
      <form
        className="nova-provider-add"
        onSubmit={(event) => {
          event.preventDefault();
          const name = newName.trim();
          const base = newBase.trim();
          if (!name || !base) return;
          void run(`Add provider ${name}`, async () => {
            await api.saveProvider(name, { base_url: base });
            setNewName("");
            setNewBase("");
          }, true);
        }}
      >
        <TextInput
          id="nova-provider-name"
          labelText="Add provider"
          placeholder="provider-name"
          value={newName}
          onChange={(event) => setNewName((event.target as HTMLInputElement).value)}
        />
        <TextInput
          id="nova-provider-base"
          labelText="Base URL"
          placeholder="https://…/v1"
          value={newBase}
          onChange={(event) => setNewBase((event.target as HTMLInputElement).value)}
        />
        <Button type="submit" disabled={busy !== null || !newName.trim() || !newBase.trim()}>
          Add
        </Button>
      </form>
      <form
        className="nova-provider-test"
        onSubmit={(event) => {
          event.preventDefault();
          const target = testTarget.trim();
          if (!target) return;
          void run(`Test ${target}`, async () => {
            const result = await api.testModel(target);
            const ok = (result as { ok?: boolean }).ok !== false;
            if (!ok) throw new Error("the model answered but not cleanly; check the note");
          }, false);
        }}
      >
        <Select
          id="nova-test-target"
          labelText="Test model"
          value={testTarget}
          onChange={(event) => setTestTarget((event.target as HTMLSelectElement).value)}
        >
          <SelectItem value="" text="Pick a target…" />
          {allTargets.map((target) => (
            <SelectItem key={target} value={target} text={target} />
          ))}
        </Select>
        <Button type="submit" disabled={busy !== null || !testTarget}>
          Test call
        </Button>
      </form>
    </section>
  );
}
