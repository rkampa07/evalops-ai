import type { ReactNode } from "react";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </header>
  );
}

export function Card({ title, children, className = "", actions }: { title?: ReactNode; children: ReactNode; className?: string; actions?: ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "bad" | "warn" }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}

type BadgeTone = "good" | "bad" | "warn" | "neutral" | "info";

export function Badge({ tone, children }: { tone: BadgeTone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function PassBadge({ passed }: { passed: boolean | null | undefined }) {
  if (passed === null || passed === undefined) return <Badge tone="neutral">n/a</Badge>;
  return passed ? <Badge tone="good">pass</Badge> : <Badge tone="bad">fail</Badge>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="loading" role="status">
      {what}…
    </div>
  );
}

export function ErrorBanner({ error, onRetry }: { error: Error | string; onRetry?: () => void }) {
  const message = typeof error === "string" ? error : error.message;
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onRetry && (
        <button type="button" className="btn btn-small" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

/** Horizontal meter for a 0..1 rate. */
export function Meter({ value, label }: { value: number | null; label?: string }) {
  if (value === null) return <span className="muted">n/a</span>;
  const tone = value >= 0.9 ? "good" : value >= 0.7 ? "warn" : "bad";
  return (
    <div className="meter" role="meter" aria-valuemin={0} aria-valuemax={1} aria-valuenow={value} aria-label={label}>
      <div className={`meter-fill meter-${tone}`} style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      <span className="meter-text">{(value * 100).toFixed(1)}%</span>
    </div>
  );
}
