import { useEffect, useState } from "react";
import { Button, Checkbox, InlineNotification, NumberInput, TextArea } from "@carbon/react";
import { api } from "../../api/client";
import type { ImageStatus, LlmState } from "../../api/types";

function parsePrices(text: string): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(/\s+/);
    if (parts.length !== 3) throw new Error(`Line "${trimmed}" must be "target in out".`);
    const [target, input, output] = parts;
    const inputPrice = Number(input);
    const outputPrice = Number(output);
    if (!Number.isFinite(inputPrice) || !Number.isFinite(outputPrice)) throw new Error(`Line "${trimmed}" has non-numeric prices.`);
    out[target] = [inputPrice, outputPrice];
  }
  return out;
}

function parseImagePrices(text: string): Record<string, number> {
  const out: Record<string, number> = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split(/\s+/);
    if (parts.length !== 2) throw new Error(`Line "${trimmed}" must be "target usd_per_image".`);
    const price = Number(parts[1]);
    if (!Number.isFinite(price)) throw new Error(`Line "${trimmed}" has a non-numeric price.`);
    out[parts[0]] = price;
  }
  return out;
}

export function Prices({ state, status }: { state: LlmState; status: ImageStatus | null }) {
  const [prices, setPrices] = useState("");
  const [imagePrices, setImagePrices] = useState("");
  const [imageMax, setImageMax] = useState(0.4);
  const [agentImageMax, setAgentImageMax] = useState(0.6);
  const [confirmPaid, setConfirmPaid] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "success"; text: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const [booted, setBooted] = useState(false);

  useEffect(() => {
    if (booted || !state) return;
    setBooted(true);
    setPrices(Object.entries(state.prices || {}).map(([target, pair]) => `${target} ${pair[0]} ${pair[1]}`).join("\n"));
    setImagePrices(Object.entries(state.image_prices || {}).map(([target, price]) => `${target} ${price}`).join("\n"));
    setImageMax(Number(state.images?.max_usd ?? 0.4));
    setAgentImageMax(Number(state.images?.agent_max_usd ?? 0.6));
    setConfirmPaid(Boolean(state.images?.confirm_paid));
  }, [booted, state]);

  return (
    <section className="nova-card" aria-label="Prices and image generation">
      <header className="nova-card-head">
        <h2>Prices and image generation</h2>
        <p className="nova-note">
          Manual prices, one per line: <code>provider:model-id input output</code> (USD per million tokens; needed for
          paid models when the provider does not publish prices). Image generation limits apply to every generated
          image, from chat and from agent tasks.
        </p>
      </header>
      {notice ? <InlineNotification kind={notice.kind} title={notice.text} onCloseButtonClick={() => setNotice(null)} lowContrast /> : null}
      {status ? (
        <p className="nova-note">
          Image generation: {status.available ? <strong>available</strong> : <strong>unavailable</strong>} via{" "}
          <code>{status.target || "—"}</code>
          {typeof status.price_per_image === "number" ? `, ${status.price_per_image} USD / image (${status.price_source})` : ""}.
        </p>
      ) : null}
      <form
        className="nova-prices-form"
        onSubmit={(event) => {
          event.preventDefault();
          setNotice(null);
          setSaving(true);
          void (async () => {
            try {
              await api.updateSettings({
                prices: parsePrices(prices),
                image_prices: parseImagePrices(imagePrices),
                image_max_usd: imageMax,
                agent_image_max_usd: agentImageMax,
                image_confirm_paid: confirmPaid,
              });
              setNotice({ kind: "success", text: "Prices and image limits saved." });
            } catch (reason) {
              setNotice({ kind: "error", text: String(reason) });
            } finally {
              setSaving(false);
            }
          })();
        }}
      >
        <TextArea
          id="nova-manual-prices"
          labelText="Manual prices"
          rows={5}
          value={prices}
          onChange={(event) => setPrices((event.target as HTMLTextAreaElement).value)}
          placeholder={"openai/gpt-4o 2.50 10.00"}
        />
        <TextArea
          id="nova-image-prices"
          labelText="Image prices"
          rows={3}
          value={imagePrices}
          onChange={(event) => setImagePrices((event.target as HTMLTextAreaElement).value)}
          placeholder={"openai/dall-e-3 0.04"}
        />
        <NumberInput
          id="nova-image-max"
          label="Max price per image (USD)"
          value={imageMax}
          onChange={(event) => setImageMax(Number((event.target as HTMLInputElement).value))}
        />
        <NumberInput
          id="nova-agent-image-max"
          label="Max price per image, agent tasks (USD)"
          value={agentImageMax}
          onChange={(event) => setAgentImageMax(Number((event.target as HTMLInputElement).value))}
        />
        <Checkbox
          id="nova-image-confirm"
          labelText="Ask before paid image generation"
          checked={confirmPaid}
          onChange={(_event, checked) => setConfirmPaid(Boolean(checked))}
        />
        <div className="nova-card-actions">
          <Button type="submit" kind="primary" disabled={saving}>
            {saving ? "Saving…" : "Save prices"}
          </Button>
        </div>
      </form>
    </section>
  );
}
