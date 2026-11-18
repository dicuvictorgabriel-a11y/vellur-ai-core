import { Button } from "@carbon/react";
import { Activity, Book, ColorPalette, Meter, Play, Roadmap, SettingsAdjust, Version } from "@carbon/icons-react";
import { PageHeader, SectionTitle } from "../components/Ui";
import { navigate } from "../router";

const TOOLS = [
  {
    title: "3D Studio",
    body: "Inspect GLB, glTF, OBJ and STL scenes from a task or this device. Opens the protected studio.",
    href: "#/studio",
    icon: <Play size={20} />,
  },
  {
    title: "GPU control",
    body: "Provider state, session limits, idle policy and the budget that stops paid calls.",
    href: "#/gpu",
    icon: <Meter size={20} />,
  },
  {
    title: "Activity ledger",
    body: "The controller event journal, releases, checks and deliveries.",
    href: "#/activity",
    icon: <Activity size={20} />,
  },
  {
    title: "Theme Studio",
    body: "Ten complete Carbon token sets, first-paint persistence and device light/dark follow.",
    href: "#/themes",
    icon: <ColorPalette size={20} />,
  },
  {
    title: "Project memory",
    body: "Knowledge records that the context bridge gives to the agent.",
    href: "#/library",
    icon: <Book size={20} />,
  },
  {
    title: "Release and rollback",
    body: "Version state, tests, owner approval and the reversible return to the previous release.",
    href: "#/release",
    icon: <Version size={20} />,
  },
  {
    title: "Server settings",
    body: "Budget ceilings, GPU policy, project memory and the write-only GitHub token.",
    href: "#/settings",
    icon: <SettingsAdjust size={20} />,
  },
  {
    title: "Mission Control",
    body: "Objective, next step, studio scenes and self-development runs.",
    href: "#/mission",
    icon: <Roadmap size={20} />,
  },
];

export function ToolsView() {
  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · Tools"
        title="Studios and system"
        description="Creative studios and the operational surfaces behind the workspace."
      />
      <div className="nova-tool-grid">
        {TOOLS.map((tool) => {
          const content = (
            <>
              <span className="nova-tool-icon">{tool.icon}</span>
              <strong>{tool.title}</strong>
              <p>{tool.body}</p>
            </>
          );
          return (
            <button key={tool.title} type="button" className="nova-tool" onClick={() => navigate(tool.href.replace(/^#\//, ""))}>
              {content}
            </button>
          );
        })}
      </div>
      <section className="nova-card">
        <SectionTitle hint="appearance">NOVA interface</SectionTitle>
        <p>
          NOVA's interface is the React app served at <code>/autopilot/</code>. The theme it uses is set in Theme
          Studio and shared with 3D Studio.
        </p>
        <div className="nova-row">
          <Button kind="ghost" href="#/themes" renderIcon={ColorPalette}>Theme Studio</Button>
        </div>
      </section>
    </div>
  );
}
