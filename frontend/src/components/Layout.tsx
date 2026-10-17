import { NavLink, Outlet } from "react-router-dom";
import { API_BASE_URL, api } from "../api/client";
import type { AuthSession, WorkspaceSummary } from "../api/types";
import { useState } from "react";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/datasets", label: "Datasets" },
  { to: "/runs/new", label: "New evaluation" },
  { to: "/runs", label: "Run history", end: true },
  { to: "/compare", label: "Compare" },
  { to: "/reports", label: "Release reports" },
  { to: "/playground", label: "Live playground" },
];

export function Layout({
  authMode,
  workspaces,
  activeWorkspaceId,
  onWorkspaceSelected,
}: {
  authMode: "oidc" | "development";
  workspaces: WorkspaceSummary[];
  activeWorkspaceId: number | null;
  onWorkspaceSelected: (session: AuthSession) => void;
}) {
  const [switchError, setSwitchError] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [creating, setCreating] = useState(false);

  async function selectWorkspace(value: string) {
    const id = Number(value);
    if (!Number.isInteger(id) || id === activeWorkspaceId) return;
    setSwitchError("");
    try {
      onWorkspaceSelected(await api.selectWorkspace(id));
    } catch {
      setSwitchError("Could not switch workspace. Your current workspace is unchanged.");
    }
  }

  async function createWorkspace() {
    const name = workspaceName.trim();
    if (!name || creating) return;
    setCreating(true);
    setSwitchError("");
    try {
      const created = await api.createWorkspace(name);
      const selected = await api.selectWorkspace(created.id);
      setWorkspaceName("");
      onWorkspaceSelected(selected);
    } catch {
      setSwitchError("Could not create the workspace.");
    } finally {
      setCreating(false);
    }
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">✓</span>
          EvalOps AI
        </div>
        <nav aria-label="Main">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        {authMode === "oidc" && workspaces.length > 0 ? (
          <section className="workspace-switcher" aria-label="Workspace">
            <label htmlFor="workspace-select">Workspace</label>
            <select
              id="workspace-select"
              value={activeWorkspaceId ?? ""}
              onChange={(event) => void selectWorkspace(event.target.value)}
            >
              {workspaces.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>
              ))}
            </select>
          </section>
        ) : authMode === "development" && workspaces[0] ? (
          <p className="muted">Workspace: {workspaces[0].name} (local)</p>
        ) : null}
        {authMode === "oidc" ? (
          <form className="workspace-create" onSubmit={(event) => { event.preventDefault(); void createWorkspace(); }}>
            <label htmlFor="workspace-name">New workspace</label>
            <input id="workspace-name" value={workspaceName} maxLength={200} onChange={(event) => setWorkspaceName(event.target.value)} />
            <button className="button small" type="submit" disabled={!workspaceName.trim() || creating}>Create</button>
          </form>
        ) : null}
        {switchError ? <p role="alert">{switchError}</p> : null}
        <div className="sidebar-foot muted">
          {authMode === "development" ? <p>Local development identity</p> : null}
          <p>API: {API_BASE_URL}</p>
          {authMode === "oidc" ? (
            <button
              className="button small"
              onClick={() => void api.logout().then(() => window.location.assign("/"))}
            >
              Sign out
            </button>
          ) : null}
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
