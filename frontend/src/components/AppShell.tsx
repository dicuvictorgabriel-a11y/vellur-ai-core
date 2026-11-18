import {
  Header,
  HeaderContainer,
  HeaderGlobalAction,
  HeaderGlobalBar,
  HeaderMenuButton,
  HeaderName,
  SideNav,
  SideNavItems,
  SideNavLink,
  SkipToContent,
} from "@carbon/react";
import {
  Activity,
  Asleep,
  Book,
  Bot,
  Chat,
  ColorPalette,
  Cube,
  Dashboard,
  Light,
  Logout,
  Meter,
  ModelAlt,
  Money,
  Music,
  Roadmap,
  Settings,
  SettingsAdjust,
  Tools,
  Version,
} from "@carbon/icons-react";
import { useState, type ReactElement } from "react";
import { AccountDialog } from "./AccountDialog";
import { Pearl, useIdentity } from "./Pearl";
import { useAuth } from "../auth";
import { useTheme } from "../theme";
import { navigate, type Route } from "../router";

interface NavItem {
  id: string;
  label: string;
  icon: ReactElement;
  group: string;
}

const NAV: NavItem[] = [
  { id: "home", label: "Home", icon: <Dashboard />, group: "Work" },
  { id: "chat", label: "Chat", icon: <Chat />, group: "Work" },
  { id: "agent", label: "Agent", icon: <Bot />, group: "Work" },
  { id: "library", label: "Library", icon: <Book />, group: "Work" },
  { id: "music", label: "Music", icon: <Music />, group: "Work" },
  { id: "tools", label: "Tools", icon: <Tools />, group: "Work" },
  { id: "studio", label: "3D Studio", icon: <Cube />, group: "Work" },
  { id: "mission", label: "Mission", icon: <Roadmap />, group: "Operate" },
  { id: "project", label: "Projects", icon: <Settings />, group: "Operate" },
  { id: "models", label: "Models", icon: <ModelAlt />, group: "Operate" },
  { id: "costs", label: "Costs", icon: <Money />, group: "Operate" },
  { id: "activity", label: "Activity", icon: <Activity />, group: "Operate" },
  { id: "gpu", label: "GPU", icon: <Meter />, group: "Operate" },
  { id: "release", label: "Release", icon: <Version />, group: "Operate" },
  { id: "settings", label: "Settings", icon: <SettingsAdjust />, group: "Operate" },
];

export function navItems() {
  return NAV;
}

export function AppShell({ route, children }: { route: Route; children: React.ReactNode }) {
  const { logout, me } = useAuth();
  const { theme, setTheme, setFollowSystem, system } = useTheme();
  const identity = useIdentity();
  const [acctOpen, setAcctOpen] = useState(false);

  const toggleMode = () => setTheme(theme.mode === "dark" ? "porcelain" : "obsidian");

  return (
    <HeaderContainer
      render={({
        isSideNavExpanded,
        onClickSideNavExpand,
      }: {
        isSideNavExpanded: boolean;
        onClickSideNavExpand: () => void;
      }) => (
        <>
          <Header aria-label="NOVA — VELLUR AI Core">
            <SkipToContent />
            <HeaderMenuButton
              aria-label={isSideNavExpanded ? "Close menu" : "Open menu"}
              onClick={onClickSideNavExpand}
              isActive={isSideNavExpanded}
              aria-expanded={isSideNavExpanded}
            />
            <HeaderName href="#/home" prefix="">
              NOVA
            </HeaderName>
            <span className="nova-header-product">VELLUR AI Core</span>
            <HeaderGlobalBar>
              <HeaderGlobalAction
                aria-label="Account"
                onClick={() => setAcctOpen(true)}
                tooltipAlignment="end"
              >
                {identity.seed ? <Pearl seed={identity.seed} className="nova-pearl-sm" /> : null}
              </HeaderGlobalAction>
              <HeaderGlobalAction
                aria-label={theme.mode === "dark" ? "Switch to a light theme" : "Switch to a dark theme"}
                onClick={toggleMode}
                tooltipAlignment="end"
              >
                {theme.mode === "dark" ? <Light /> : <Asleep />}
              </HeaderGlobalAction>
              <HeaderGlobalAction
                aria-label="Open Theme Studio"
                onClick={() => navigate("themes")}
                tooltipAlignment="end"
              >
                <ColorPalette />
              </HeaderGlobalAction>
              {me?.logged_in ? (
                <HeaderGlobalAction aria-label="Sign out" onClick={() => void logout()} tooltipAlignment="end">
                  <Logout />
                </HeaderGlobalAction>
              ) : null}
            </HeaderGlobalBar>
          </Header>
          <SideNav
            aria-label="NOVA navigation"
            expanded={isSideNavExpanded}
            onOverlayClick={onClickSideNavExpand}
            isChildOfHeader
          >
            <SideNavItems>
              {["Work", "Operate"].map((group) => (
                <div key={group} className="nova-nav-group">
                  <span className="nova-nav-group-label">{group}</span>
                  {NAV.filter((item) => item.group === group).map((item) => (
                    <SideNavLink
                      key={item.id}
                      href={`#/${item.id}`}
                      renderIcon={() => item.icon}
                      isActive={route.name === item.id}
                      large
                    >
                      {item.label}
                    </SideNavLink>
                  ))}
                </div>
              ))}
              <div className="nova-nav-group">
                <span className="nova-nav-group-label">System</span>
                <SideNavLink href="#/themes" renderIcon={() => <ColorPalette />} isActive={route.name === "themes"} large>
                  Theme Studio
                </SideNavLink>
                <button
                  type="button"
                  className="nova-nav-button"
                  onClick={() => setFollowSystem(!system)}
                >
                  {system ? "Stop following device" : "Follow device light/dark"}
                </button>
              </div>
            </SideNavItems>
          </SideNav>
          <main id="main-content" className="nova-main" tabIndex={-1}>
            {children}
          </main>
          <AccountDialog open={acctOpen} onClose={() => setAcctOpen(false)} />
        </>
      )}
    />
  );
}
