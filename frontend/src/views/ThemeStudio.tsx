import { Checkbox, Select, SelectItem } from "@carbon/react";
import { Checkmark } from "@carbon/icons-react";
import { PageHeader, SectionTitle } from "../components/Ui";
import { THEMES, useTheme } from "../theme";

export function ThemeStudio() {
  const { theme, preference, system, setTheme, setSystem, setFollowSystem } = useTheme();
  const darkId = preference.startsWith("system:") ? preference.slice(7).split("/")[0] : theme.mode === "dark" ? theme.id : "obsidian";
  const lightId = preference.startsWith("system:") ? preference.slice(7).split("/")[1] : theme.mode === "light" ? theme.id : "porcelain";
  const darkThemes = THEMES.filter((item) => item.mode === "dark");
  const lightThemes = THEMES.filter((item) => item.mode === "light");

  return (
    <div className="nova-view">
      <PageHeader
        kicker="NOVA · System"
        title="Theme Studio"
        description="Ten complete Carbon token sets. The choice is stored in the nova_theme cookie for the first paint and in the controller setting for every device."
      />
      <div className="nova-card nova-theme-controls">
        <Checkbox
          id="follow-system"
          labelText="Follow the device light/dark preference"
          checked={system}
          onChange={(_: unknown, { checked }: { checked: boolean }) => setFollowSystem(Boolean(checked))}
        />
        {system ? (
          <div className="nova-theme-system">
            <Select
              id="day-theme"
              labelText="Day theme"
              value={lightId}
              onChange={(event) => setSystem(darkId, event.target.value)}
            >
              {lightThemes.map((item) => (
                <SelectItem key={item.id} value={item.id} text={item.name} />
              ))}
            </Select>
            <Select
              id="night-theme"
              labelText="Night theme"
              value={darkId}
              onChange={(event) => setSystem(event.target.value, lightId)}
            >
              {darkThemes.map((item) => (
                <SelectItem key={item.id} value={item.id} text={item.name} />
              ))}
            </Select>
          </div>
        ) : null}
      </div>
      {(["nova", "carbon"] as const).map((group) => (
        <section key={group}>
          <SectionTitle hint={group === "nova" ? "Original NOVA token sets" : "Official IBM Carbon themes"}>
            {group === "nova" ? "NOVA" : "IBM Carbon"}
          </SectionTitle>
          <div className="nova-theme-grid" role="radiogroup" aria-label={group === "nova" ? "NOVA themes" : "Carbon themes"}>
            {THEMES.filter((item) => item.group === group).map((item) => (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={theme.id === item.id}
                className={`nova-theme-card${theme.id === item.id ? " is-active" : ""}`}
                onClick={() => setTheme(item.id)}
                data-nova-preview={item.id}
              >
                <span className="nova-theme-preview" data-nova-preview={item.id}>
                  <span className="tp-bar" />
                  <span className="tp-side" />
                  <span className="tp-body">
                    <span className="tp-line" />
                    <span className="tp-line short" />
                    <span className="tp-accent" />
                  </span>
                </span>
                <span className="nova-theme-meta">
                  <strong>{item.name}</strong>
                  <small>{item.note}</small>
                  <em>{item.mode}</em>
                </span>
                {theme.id === item.id ? <Checkmark className="nova-theme-check" size={16} /> : null}
              </button>
            ))}
          </div>
        </section>
      ))}
      <p className="nova-note">
        Current preference: <code>{preference}</code>. It is stored in the <code>nova_theme</code> cookie and the
        <code>ui_theme</code> server setting, so every NOVA screen stays visually aligned.
      </p>
    </div>
  );
}
