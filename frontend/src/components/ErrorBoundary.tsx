import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@carbon/react";

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("NOVA React error boundary", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="nova-crash" role="alert">
          <span className="nova-kicker">Recoverable error</span>
          <h1>This screen stopped unexpectedly</h1>
          <p>Your data is unaffected. Reload the page, or return to the last working screen.</p>
          <pre>{this.state.error.message}</pre>
          <div className="nova-row">
            <Button kind="primary" onClick={() => window.location.reload()}>
              Reload
            </Button>
            <Button kind="tertiary" href="/autopilot/">
              Open classic NOVA
            </Button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
