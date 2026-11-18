import { useEffect, useState } from "react";
import { InlineLoading, Modal, TextInput } from "@carbon/react";
import { Pearl, useIdentity } from "./Pearl";
import { errorMessage } from "../utils";

export function AccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const identity = useIdentity();
  const [name, setName] = useState(identity.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName(identity.name);
      setError("");
    }
  }, [open, identity.name]);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await identity.saveName(name);
      onClose();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      onRequestClose={onClose}
      modalHeading="Account"
      modalLabel="NOVA"
      primaryButtonText={busy ? "Saving…" : "Save name"}
      secondaryButtonText="Close"
      onRequestSubmit={() => void save()}
      primaryButtonDisabled={busy}
    >
      <div className="nova-acct-head">
        {identity.seed ? <Pearl seed={identity.seed} className="nova-pearl-acct" /> : null}
        <div>
          <strong>{identity.name || "Owner"}</strong>
          <small>Artist · NOVA</small>
        </div>
      </div>
      <TextInput
        id="nova-owner-name"
        labelText="Display name"
        placeholder="Owner"
        value={name}
        onChange={(event) => setName((event.target as HTMLInputElement).value)}
      />
      {busy ? <InlineLoading description="Saving…" /> : null}
      {error ? <p className="nova-error">{error}</p> : null}
    </Modal>
  );
}
