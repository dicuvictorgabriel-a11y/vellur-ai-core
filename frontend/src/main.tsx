import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@carbon/styles/css/styles.css";
import "../../autopilot/app/static/carbon/carbon-tokens.css";
import "../../autopilot/app/static/carbon/nova-themes.css";
import "./styles/app.css";
import { App } from "./App";
import { AuthProvider } from "./auth";
import { ThemeProvider } from "./theme";
import { ErrorBoundary } from "./components/ErrorBoundary";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <AuthProvider>
        <ErrorBoundary>
          <App />
        </ErrorBoundary>
      </AuthProvider>
    </ThemeProvider>
  </StrictMode>,
);
