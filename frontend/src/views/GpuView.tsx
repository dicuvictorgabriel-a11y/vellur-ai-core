import { useState } from "react";
import { Button, InlineNotification, Tag, TextInput } from "@carbon/react";
import { Meter, Play, Renew, Stop } from "@carbon/icons-react";
import { api } from "../api/client";
import { LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { dateTime, duration, errorMessage, money } from "../utils";

export function GpuView() {
  const status = useAsync(() => api.status(), []);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [failed, setFailed] = useState(false);
  const [hyperaiHost, setHyperaiHost] = useState("ssh.hyper.ai");
  const [hyperaiPort, setHyperaiPort] = useState("");
  const [vastOffer, setVastOffer] = useState<Record<string, unknown> | null>(null);

  if (status.loading && !status.data) return <Loading label="Reading GPU state" />;
  if (status.error) return <LoadError message={status.error} onRetry={status.reload} />;
  const s = status.data!;
  const session = s.session;
  const settings = s.settings || {};

  const run = async (label: string, action: () => Promise<unknown>, confirmation?: string, withResult?: (result: unknown) => void) => {
    if (confirmation && !window.confirm(confirmation)) return;
    setBusy(label);
    setNotice("");
    setFailed(false);
    try {
      const result = await action();
      setNotice(`${label} accepted by the controller.`);
      status.reload();
      withResult?.(result);
    } catch (reason) {
      setFailed(true);
      setNotice(errorMessage(reason));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · GPU"
        title="Inference control"
        description="The controller owns the GPU lifecycle. This screen reads the same state as vac."
        actions={<Button kind="tertiary" renderIcon={Renew} onClick={status.reload}>Refresh</Button>}
      />
      <section className="nova-stat-grid">
        <Stat label="Phase" value={<Tag type={s.phase === "offline" ? "red" : "green"}>{s.phase}</Tag>} detail={s.explain || "controller state"} tone={s.phase === "offline" ? "bad" : "ok"} />
        <Stat label="Live mode" value={s.live_mode ? "Enabled" : "Dry run"} detail={s.live_mode ? "paid starts are possible" : "paid starts are refused"} tone={s.live_mode ? "warn" : "ok"} />
        <Stat label="In flight" value={String(s.inflight)} detail={`${s.queued} queued`} />
        <Stat label="Idle stop" value={session ? duration(s.idle_shutdown_in_s) : "—"} detail={`after ${settings.idle_timeout_min ?? "—"} min idle`} />
      </section>
      {notice ? (
        <InlineNotification
          kind={failed ? "error" : "success"}
          lowContrast
          title={failed ? "Action failed" : "Controller responded"}
          subtitle={notice}
          onCloseButtonClick={() => setNotice("")}
        />
      ) : null}

      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={session ? "attached" : "no active session"}>Session</SectionTitle>
          {session ? (
            <>
              <div className="nova-money">
                <div><span>Provider</span><strong>{String(session.provider || "—")}</strong></div>
                <div><span>GPU</span><strong>{String(session.gpu || session.gpu_name || "—")}</strong></div>
                <div><span>Rate</span><strong>{money(Number(session.price_per_hour ?? session.rate_usd_per_hour ?? 0))}/h</strong></div>
                <div><span>Started</span><strong>{dateTime(session.started_at)}</strong></div>
              </div>
              <dl className="nova-kv">
                <div><dt>Profile</dt><dd>{String(session.profile || settings.profile || "—")}</dd></div>
                <div><dt>Address</dt><dd>{String(session.address || session.ssh_host || "—")}</dd></div>
                <div><dt>VRAM</dt><dd>{session.vram_gb ? `${session.vram_gb} GB` : "—"}</dd></div>
                <div><dt>Protected until</dt><dd>{s.protected_until ? dateTime(s.protected_until) : "—"}</dd></div>
                <div><dt>Termination</dt><dd>{s.termination || "—"}</dd></div>
              </dl>
            </>
          ) : (
            <p className="nova-note">{s.explain || "No GPU is attached. Inference to the vellur roles uses the routed provider targets."}</p>
          )}
          <div className="nova-row">
            <Button
              kind="secondary"
              renderIcon={Meter}
              disabled={Boolean(busy)}
              onClick={() =>
                void run("Protect 30 minutes", () => api.protectGpu(30), "Keep the GPU awake for 30 minutes? This does not start a new paid session.")
              }
            >
              Protect 30 min
            </Button>
            <Button
              kind="danger"
              renderIcon={Stop}
              disabled={!session || !s.live_mode || Boolean(busy)}
              onClick={() => void run("Stop", () => api.stopGpu(), "Stop the current GPU session now? Work in flight can fail.")}
            >
              Stop session
            </Button>
            <Button
              kind="primary"
              renderIcon={Play}
              disabled={!s.live_mode || Boolean(busy) || Boolean(session)}
              onClick={() =>
                void run(
                  "Start",
                  () => api.startGpu(String(settings.preferred_provider || "vastai")),
                  "Start a paid GPU session? This can cost real money and is disabled in dry run.",
                )
              }
            >
              Start session
            </Button>
          </div>
          {!session && s.live_mode ? (
            <p className="nova-note">Start uses the preferred provider from settings. Check the budget and rate before confirming.</p>
          ) : null}
          {!s.live_mode ? (
            <InlineNotification
              kind="info"
              lowContrast
              title="Dry run"
              subtitle="Starting and stopping are refused by the controller until the owner enables live actions with sudo vac autopilot live on."
              hideCloseButton
            />
          ) : null}
        </section>

        <section className="nova-card">
          <SectionTitle hint="controller view">Providers</SectionTitle>
          <ul className="nova-provider-list">
            {(s.providers || []).map((provider) => (
              <li key={provider.provider} className="nova-provider">
                <div>
                  <strong>{provider.provider}</strong>
                  <small>{provider.detail || (provider.automated ? "automated lifecycle" : "assisted lifecycle")}</small>
                </div>
                <Tag type={provider.configured ? "green" : "gray"}>{provider.configured ? "configured" : "missing"}</Tag>
              </li>
            ))}
          </ul>
          <dl className="nova-kv">
            <div><dt>Preferred</dt><dd>{String(settings.preferred_provider || "—")}</dd></div>
            <div><dt>Profile</dt><dd>{String(settings.profile || "—")}</dd></div>
            <div><dt>Auto start paid</dt><dd>{settings.auto_start_paid ? "yes" : "no"}</dd></div>
            <div><dt>Max hourly</dt><dd>{money(Number(settings.max_hourly_usd ?? 0))}</dd></div>
            <div><dt>Max session</dt><dd>{String(settings.max_session_hours ?? "—")} h</dd></div>
            <div><dt>HyperAI SSH</dt><dd>{String(settings.hyperai_ssh_host || "—")}:{String(settings.hyperai_ssh_port || "—")}</dd></div>
          </dl>
        </section>

        <section className="nova-card">
          <SectionTitle hint="HyperAI assisted">Connect HyperAI</SectionTitle>
          <p className="nova-note">
            Paste the SSH host and port of a HyperAI workspace (the port changes on every restart of the workspace).
            The controller then opens its own tunnel to it; the workspace never gets a credential for this server.
          </p>
          <form
            className="nova-hyperai-form"
            onSubmit={(event) => {
              event.preventDefault();
              void run(
                "Connect",
                () => api.hyperaiConnect(hyperaiHost, Number(hyperaiPort)),
                "Attach this HyperAI workspace as the GPU session?",
              );
            }}
          >
            <TextInput
              id="nova-hyperai-host"
              labelText="SSH host"
              placeholder="ssh.hyper.ai"
              value={hyperaiHost}
              onChange={(event) => setHyperaiHost((event.target as HTMLInputElement).value)}
            />
            <TextInput
              id="nova-hyperai-port"
              labelText="SSH port"
              type="number"
              placeholder="22220"
              value={hyperaiPort}
              onChange={(event) => setHyperaiPort((event.target as HTMLInputElement).value)}
            />
            <Button type="submit" disabled={Boolean(busy) || !hyperaiHost || !Number(hyperaiPort)}>
              Connect
            </Button>
          </form>
        </section>

        <section className="nova-card">
          <SectionTitle hint="non-billable">Vast.ai offers</SectionTitle>
          <p className="nova-note">
            Runs a credential check and an offer search for the current profile. Nothing is rented; no instance is
            created.
          </p>
          <Button
            disabled={Boolean(busy)}
            onClick={() => void run("Check", () => api.providerCheck("vastai"), undefined, (result) => setVastOffer(result as Record<string, unknown>))}
          >
            Check offers
          </Button>
          {vastOffer ? (
            <pre className="nova-offer">{JSON.stringify(vastOffer, null, 2)}</pre>
          ) : null}
        </section>
      </div>
      <p className="nova-note">
        HyperAI profile changes and budget editing live in Settings; this screen only connects workspaces and checks
        offers.
      </p>
    </div>
  );
}
