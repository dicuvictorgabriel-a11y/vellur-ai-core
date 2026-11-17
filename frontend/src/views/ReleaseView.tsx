import { useState } from "react";
import { Button, InlineNotification, Tag } from "@carbon/react";
import { Renew } from "@carbon/icons-react";
import { api } from "../api/client";
import { LoadError, Loading, PageHeader, SectionTitle, Stat } from "../components/Ui";
import { useAsync } from "../hooks";
import { dateOnly, errorMessage, tagType } from "../utils";

export function ReleaseView() {
  const release = useAsync(() => api.release(), []);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const data = release.data;
  const latest = data?.latest;
  const canApprove = Boolean(latest?.sha && latest.sha !== data?.installed && latest.sha !== data?.approved && latest.ci === "passed");

  const act = async (label: string, action: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await action();
      setNote(label);
      release.reload();
    } catch (reason) {
      setError(errorMessage(reason));
    } finally {
      setBusy(false);
    }
  };

  const approve = () => {
    const sha = latest?.sha;
    if (!sha) return;
    if (!window.confirm(`Approve version ${sha.slice(0, 7)} for installation?\n\nThe server installs it only after the automated tests passed, with automatic restore on failure.`)) return;
    void act("Approval recorded. The server will install this version.", () => api.approveRelease(sha));
  };

  if (release.loading && !data) return <Loading label="Reading release state" />;
  if (release.error) return <LoadError message={release.error} onRetry={release.reload} />;

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Release"
        title="Versions and rollback"
        description="Owner approval is required before the server installs a version. Nothing on this page installs immediately by itself."
        actions={<Button kind="ghost" renderIcon={Renew} onClick={release.reload}>Refresh</Button>}
      />
      {note ? <InlineNotification kind="success" lowContrast title="Release request" subtitle={note} hideCloseButton /> : null}
      {error ? <InlineNotification kind="error" lowContrast title="Release action failed" subtitle={error} hideCloseButton /> : null}
      <section className="nova-stat-grid">
        <Stat label="Installed" value={data?.installed ? data.installed.slice(0, 7) : "not recorded"} detail={data?.last || "no installer report"} />
        <Stat label="Newest on GitHub" value={latest?.sha ? latest.sha.slice(0, 7) : "unknown"} detail={latest?.message || data?.error || "GitHub unavailable"} />
        <Stat label="Tests" value={String(latest?.ci || "unknown")} detail="passing tests are required" tone={latest?.ci === "passed" ? "ok" : "warn"} />
        <Stat label="Installer" value={data?.installer_active ? "active" : "inactive"} detail={data?.installer_active ? "server reports recent activity" : "run sudo vac autoupdate on"} tone={data?.installer_active ? "ok" : "warn"} />
      </section>
      <div className="nova-columns">
        <section className="nova-card nova-span-2">
          <SectionTitle hint={data?.branch}>Release state</SectionTitle>
          <dl className="nova-kv">
            <div><dt>Repository</dt><dd>{data?.repo || "—"}</dd></div>
            <div><dt>Approved</dt><dd>{data?.approved ? data.approved.slice(0, 12) : "—"}</dd></div>
            <div><dt>Approved at</dt><dd>{data?.approved_at ? dateOnly(data.approved_at) : "—"}</dd></div>
            <div><dt>Rollback pending</dt><dd><Tag type={tagType(data?.request === "rollback" ? "warn" : "idle")}>{data?.request || "none"}</Tag></dd></div>
            <div><dt>Previous version</dt><dd>{data?.previous || "—"}</dd></div>
          </dl>
          <div className="nova-row">
            <Button kind="primary" disabled={!canApprove || busy} onClick={approve}>
              Install version {latest?.sha ? latest.sha.slice(0, 7) : ""}
            </Button>
            <Button kind="ghost" disabled={!data?.approved || busy} onClick={() => void act("Approval cancelled.", () => api.cancelRelease())}>
              Cancel approval
            </Button>
            <Button
              kind="danger--tertiary"
              disabled={!data?.previous || busy}
              onClick={() => {
                if (!window.confirm("Ask the server to return to the previous version of NOVA?")) return;
                void act("Rollback requested; the server applies it within two minutes.", () => api.rollbackRelease());
              }}
            >
              Return to the previous version
            </Button>
          </div>
          {latest && latest.ci !== "passed" ? <p className="nova-note">Approval is disabled until the automated tests for this commit pass.</p> : null}
          {!data?.installer_active ? <p className="nova-note">The installer is not reporting activity; approval can be recorded but no installation will happen until it is enabled.</p> : null}
        </section>
      </div>
    </div>
  );
}
