import { Button, InlineLoading, InlineNotification } from "@carbon/react";
import { Renew, Warning } from "@carbon/icons-react";
import type { ReactNode } from "react";

export function PageHeader({
  kicker,
  title,
  description,
  actions,
}: {
  kicker: string;
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="nova-pagehead">
      <div>
        <span className="nova-kicker">{kicker}</span>
        <h1>{title}</h1>
        {description ? <p>{description}</p> : null}
      </div>
      {actions ? <div className="nova-pagehead-actions">{actions}</div> : null}
    </header>
  );
}

export function SectionTitle({ children, hint }: { children: ReactNode; hint?: ReactNode }) {
  return (
    <div className="nova-sectiontitle">
      <h2>{children}</h2>
      {hint ? <span>{hint}</span> : null}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="nova-loading">
      <InlineLoading description={label} status="active" />
    </div>
  );
}

export function LoadError({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="nova-loaderror">
      <InlineNotification kind="error" lowContrast title="Could not load" subtitle={message} hideCloseButton />
      {onRetry ? (
        <Button kind="ghost" size="sm" renderIcon={Renew} onClick={onRetry}>
          Retry
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title, detail, action }: { title: string; detail?: string; action?: ReactNode }) {
  return (
    <div className="nova-empty">
      <Warning size={20} />
      <div>
        <strong>{title}</strong>
        {detail ? <p>{detail}</p> : null}
        {action}
      </div>
    </div>
  );
}

export function Stat({
  label,
  value,
  detail,
  tone,
}: {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
  tone?: "ok" | "warn" | "bad" | "accent";
}) {
  return (
    <div className={`nova-stat${tone ? ` is-${tone}` : ""}`}>
      <span className="nova-stat-label">{label}</span>
      <strong className="nova-stat-value">{value}</strong>
      {detail ? <span className="nova-stat-detail">{detail}</span> : null}
    </div>
  );
}
