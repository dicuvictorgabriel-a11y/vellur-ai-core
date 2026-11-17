import { Suspense, lazy } from "react";
import { AppShell } from "./components/AppShell";
import { ChangePasswordScreen, LoginScreen } from "./components/AuthScreens";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Loading } from "./components/Ui";
import { useAuth } from "./auth";
import { useHashRoute } from "./router";

const HomeView = lazy(() => import("./views/HomeView").then((m) => ({ default: m.HomeView })));
const ChatView = lazy(() => import("./views/ChatView").then((m) => ({ default: m.ChatView })));
const AgentView = lazy(() => import("./views/AgentView").then((m) => ({ default: m.AgentView })));
const ModelsView = lazy(() => import("./views/ModelsView").then((m) => ({ default: m.ModelsView })));
const CostsView = lazy(() => import("./views/CostsView").then((m) => ({ default: m.CostsView })));
const ActivityView = lazy(() => import("./views/ActivityView").then((m) => ({ default: m.ActivityView })));
const GpuView = lazy(() => import("./views/GpuView").then((m) => ({ default: m.GpuView })));
const LibraryView = lazy(() => import("./views/LibraryView").then((m) => ({ default: m.LibraryView })));
const MusicView = lazy(() => import("./views/MusicView").then((m) => ({ default: m.MusicView })));
const ToolsView = lazy(() => import("./views/ToolsView").then((m) => ({ default: m.ToolsView })));
const MissionView = lazy(() => import("./views/MissionView").then((m) => ({ default: m.MissionView })));
const ProjectView = lazy(() => import("./views/ProjectView").then((m) => ({ default: m.ProjectView })));
const ThemeStudio = lazy(() => import("./views/ThemeStudio").then((m) => ({ default: m.ThemeStudio })));
const SettingsView = lazy(() => import("./views/SettingsView").then((m) => ({ default: m.SettingsView })));
const ReleaseView = lazy(() => import("./views/ReleaseView").then((m) => ({ default: m.ReleaseView })));
const StudioView = lazy(() => import("./views/StudioView").then((m) => ({ default: m.StudioView })));

export function App() {
  const { me, loading } = useAuth();
  const route = useHashRoute();

  if (loading) {
    return (
      <main className="nova-boot" id="main-content">
        <span className="nova-kicker">VELLUR AI Core</span>
        <h1>NOVA</h1>
        <Loading label="Opening the controller" />
      </main>
    );
  }
  if (!me?.logged_in) return <LoginScreen />;
  if (me.must_change) return <ChangePasswordScreen />;

  const view = (() => {
    switch (route.name) {
      case "chat":
        return <ChatView route={route} />;
      case "agent":
        return <AgentView route={route} />;
      case "models":
        return <ModelsView />;
      case "costs":
        return <CostsView />;
      case "activity":
        return <ActivityView />;
      case "gpu":
        return <GpuView />;
      case "library":
        return <LibraryView route={route} />;
      case "music":
        return <MusicView route={route} />;
      case "tools":
        return <ToolsView />;
      case "mission":
        return <MissionView />;
      case "project":
        return <ProjectView route={route} />;
      case "themes":
        return <ThemeStudio />;
      case "settings":
        return <SettingsView />;
      case "release":
        return <ReleaseView />;
      case "studio":
        return <StudioView />;
      default:
        return <HomeView />;
    }
  })();

  return (
    <AppShell route={route}>
      <ErrorBoundary key={route.name}>
        <Suspense fallback={<Loading label="Loading view" />}>{view}</Suspense>
      </ErrorBoundary>
    </AppShell>
  );
}
