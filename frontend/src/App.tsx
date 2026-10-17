import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes } from "react-router-dom";
import { API_BASE_URL, ApiError, api } from "./api/client";
import type { AuthSession } from "./api/types";
import { Layout } from "./components/Layout";
import { Empty } from "./components/ui";
import { Compare } from "./pages/Compare";
import { DatasetDetail } from "./pages/DatasetDetail";
import { Datasets } from "./pages/Datasets";
import { NewRun } from "./pages/NewRun";
import { Playground } from "./pages/Playground";
import { Overview } from "./pages/Overview";
import { Reports } from "./pages/Reports";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";

export function App() {
  const [authState, setAuthState] = useState<"loading" | "authenticated" | "unauthenticated" | "error">("loading");
  const [authError, setAuthError] = useState("");
  const [authMode, setAuthMode] = useState<"oidc" | "development">("oidc");
  const [session, setSession] = useState<AuthSession | null>(null);

  const checkSession = useCallback(() => {
    setAuthState("loading");
    setAuthError("");
    void api.authSession().then(
      (session) => {
        setSession(session);
        setAuthMode(session.mode);
        setAuthState(session.authenticated ? "authenticated" : "unauthenticated");
      },
      (error: unknown) => {
        setAuthError(error instanceof ApiError ? error.message : "Could not check the login session.");
        setAuthState("error");
      },
    );
  }, []);

  useEffect(() => {
    checkSession();
  }, [checkSession]);

  if (authState === "loading") return <main className="content" aria-busy="true">Checking sign-in…</main>;
  if (authState === "error") {
    return <main className="content"><p role="alert">{authError}</p><button onClick={checkSession}>Retry</button></main>;
  }
  if (authState === "unauthenticated") {
    return (
      <main className="content">
        <h1>Sign in to EvalOps AI</h1>
        <p>Continue with your configured identity provider.</p>
        <a className="button primary" href={`${API_BASE_URL}/auth/login`}>Sign in</a>
      </main>
    );
  }
  return (
    <Routes key={session?.active_workspace_id ?? "no-workspace"}>
      <Route
        element={
          <Layout
            authMode={authMode}
            workspaces={session?.workspaces ?? []}
            activeWorkspaceId={session?.active_workspace_id ?? null}
            onWorkspaceSelected={(next) => setSession(next)}
          />
        }
      >
        <Route index element={<Overview />} />
        <Route path="datasets" element={<Datasets />} />
        <Route path="datasets/:id" element={<DatasetDetail />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/new" element={<NewRun />} />
        <Route path="runs/:id" element={<RunDetail />} />
        <Route path="compare" element={<Compare />} />
        <Route path="playground" element={<Playground />} />
        <Route path="reports" element={<Reports />} />
        <Route
          path="*"
          element={
            <Empty>
              Page not found. <Link to="/">Back to overview</Link>
            </Empty>
          }
        />
      </Route>
    </Routes>
  );
}
