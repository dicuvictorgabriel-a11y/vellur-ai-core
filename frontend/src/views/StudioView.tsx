import { Button } from "@carbon/react";
import { Launch } from "@carbon/icons-react";
import { PageHeader } from "../components/Ui";

export function StudioView() {
  return (
    <div className="nova-view nova-studio-view">
      <PageHeader
        kicker="NOVA · 3D Studio"
        title="Scenes"
        description="The existing 3D Studio runs unchanged inside NOVA. It keeps its own scene API, editor and GLB export."
        actions={
          <Button kind="ghost" renderIcon={Launch} onClick={() => window.open("/autopilot/3d/", "_blank", "noopener")}>
            Open standalone
          </Button>
        }
      />
      <section className="nova-card nova-studio-frame">
        <iframe src="/autopilot/3d/" title="NOVA 3D Studio" />
      </section>
      <p className="nova-note">
        Embedded same-origin preview. The React shell supplies navigation, themes and login; the 3D editor itself remains the
        verified legacy implementation until its scene contracts are ported.
      </p>
    </div>
  );
}
