import { Link, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Empty } from "./components/ui";
import { Compare } from "./pages/Compare";
import { DatasetDetail } from "./pages/DatasetDetail";
import { Datasets } from "./pages/Datasets";
import { NewRun } from "./pages/NewRun";
import { Overview } from "./pages/Overview";
import { Reports } from "./pages/Reports";
import { RunDetail } from "./pages/RunDetail";
import { Runs } from "./pages/Runs";

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Overview />} />
        <Route path="datasets" element={<Datasets />} />
        <Route path="datasets/:id" element={<DatasetDetail />} />
        <Route path="runs" element={<Runs />} />
        <Route path="runs/new" element={<NewRun />} />
        <Route path="runs/:id" element={<RunDetail />} />
        <Route path="compare" element={<Compare />} />
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
