import { NavLink, Outlet } from "react-router-dom";
import { API_BASE_URL } from "../api/client";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/datasets", label: "Datasets" },
  { to: "/runs/new", label: "New evaluation" },
  { to: "/runs", label: "Run history", end: true },
  { to: "/compare", label: "Compare" },
  { to: "/reports", label: "Release reports" },
];

export function Layout() {
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
        <div className="sidebar-foot muted">API: {API_BASE_URL}</div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
