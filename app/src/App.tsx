/** App shell: top bar with engine status, routes, offline screen, drop overlay and toasts. */
import { NavLink, Route, Routes } from "react-router";
import { useEngineEvents } from "./api/useEngineEvents";
import { focusRing } from "./components/ui/Button";
import { Toaster } from "./components/ui/Toast";
import { EngineOffline, EngineStatus } from "./features/engine/EngineStatus";
import { HomePage } from "./features/home/HomePage";
import { useFileDrop } from "./features/home/useFileDrop";
import { useImportVideo } from "./features/home/useImportVideo";
import { ProjectPage } from "./features/project/ProjectPage";
import { SettingsPage } from "./features/settings/SettingsPage";
import { useUiStore } from "./stores/ui";

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded px-2 py-1 ${isActive ? "text-text" : "text-muted hover:text-text"} ${focusRing}`;

export default function App() {
  const retry = useEngineEvents();
  const offline = useUiStore((s) => s.connection === "offline");
  const { importPath } = useImportVideo();
  const dragging = useFileDrop(importPath);

  return (
    <div className="flex h-screen flex-col bg-bg text-text">
      <header className="flex items-center gap-4 border-b border-border px-4 py-2">
        <span className="font-semibold">ClipApp</span>
        <nav aria-label="Principal" className="flex gap-1 text-sm">
          <NavLink to="/" end className={navClass}>
            Inicio
          </NavLink>
          <NavLink to="/settings" className={navClass}>
            Ajustes
          </NavLink>
        </nav>
        <div className="ml-auto">
          <EngineStatus />
        </div>
      </header>
      <main className="flex-1 overflow-auto">
        {offline ? (
          <EngineOffline onRetry={retry} />
        ) : (
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/projects/:id" element={<ProjectPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        )}
      </main>
      {dragging && (
        <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center border-4 border-dashed border-accent bg-bg/80 text-lg">
          Suelta el video para importarlo
        </div>
      )}
      <Toaster />
    </div>
  );
}
