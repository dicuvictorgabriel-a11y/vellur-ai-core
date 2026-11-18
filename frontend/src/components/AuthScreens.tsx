import { useEffect, useState } from "react";
import { Button, InlineNotification, PasswordInput } from "@carbon/react";
import { Login as LoginIcon, UserAvatar } from "@carbon/icons-react";
import { useAuth } from "../auth";

export function LoginScreen() {
  const { login, error } = useAuth();
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(error);

  useEffect(() => setMessage(error), [error]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    try {
      await login(password);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="nova-auth" id="main-content">
      <div className="nova-auth-brand">
        <span className="nova-auth-mark" aria-hidden="true">
          N
        </span>
        <span className="nova-kicker">VELLUR AI Core</span>
        <h1>NOVA</h1>
        <p>The owner workspace for models, agents, costs and releases.</p>
      </div>
      <form className="nova-auth-card" onSubmit={submit}>
        <UserAvatar size={24} />
        <h2>Sign in</h2>
        <p>Use the controller password. Sessions stay on this device.</p>
        {message ? <InlineNotification kind="error" lowContrast title="Sign in failed" subtitle={message} hideCloseButton /> : null}
        <PasswordInput
          id="nova-password"
          labelText="Password"
          autoComplete="current-password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          required
        />
        <Button type="submit" renderIcon={LoginIcon} disabled={busy || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}

export function ChangePasswordScreen() {
  const { changePassword, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (next !== confirm) {
      setMessage("The two new passwords do not match.");
      return;
    }
    if (next.length < 8) {
      setMessage("The new password needs at least 8 characters.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      await changePassword(current, next);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "Could not change the password");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="nova-auth" id="main-content">
      <div className="nova-auth-brand">
        <span className="nova-kicker">VELLUR AI Core</span>
        <h1>Set a new password</h1>
        <p>The temporary password has to be replaced before the workspace opens.</p>
      </div>
      <form className="nova-auth-card" onSubmit={submit}>
        <h2>Change password</h2>
        {message ? <InlineNotification kind="error" lowContrast title="Not changed" subtitle={message} hideCloseButton /> : null}
        <PasswordInput id="current-password" labelText="Current password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        <PasswordInput id="new-password" labelText="New password" value={next} onChange={(e) => setNext(e.target.value)} required />
        <PasswordInput id="confirm-password" labelText="Repeat new password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
        <div className="nova-row">
          <Button type="submit" disabled={busy || !current || !next}>
            {busy ? "Saving…" : "Save password"}
          </Button>
          <Button kind="ghost" onClick={() => void logout()}>
            Sign out
          </Button>
        </div>
      </form>
    </main>
  );
}
