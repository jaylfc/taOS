import { useEffect, useState } from "react";
import { formatRate, loadPrivacy, savePrivacy, type Privacy } from "../privacy";
import { OobePage } from "../OobeShell";

/**
 * What stays on the device and what goes online, with the two background
 * connections the user can turn off here. Never blocks setup: a setting that
 * cannot be read is left to Settings, but a switch the user moved must be
 * saved before setup moves on, or the screen would promise an opt-out that
 * did not happen.
 */
export function PrivacyStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const [loaded, setLoaded] = useState<Privacy | null>(null);
  const [ping, setPing] = useState(true);
  const [sharing, setSharing] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    void loadPrivacy().then((p) => {
      if (!live) return;
      setLoaded(p);
      setPing(p.ping?.enabled ?? true);
      setSharing(p.sharing?.enabled ?? true);
    });
    return () => {
      live = false;
    };
  }, []);

  async function submit() {
    if (!loaded || busy) return;
    setBusy(true);
    setError("");
    try {
      await savePrivacy(loaded, ping, sharing);
    } catch (e) {
      setBusy(false);
      setError(e instanceof Error ? e.message : "Couldn't save. Try again.");
      return;
    }
    setBusy(false);
    onNext();
  }

  const rate = loaded ? formatRate(loaded.uploadKbps) : null;

  return (
    <OobePage
      title="Privacy"
      subtitle="Your chats, files, memory and agents stay on this device. Here is what goes online."
      onBack={onBack}
      onSubmit={() => void submit()}
      footer={
        <button type="submit" className="oobe-primary" disabled={!loaded || busy} aria-busy={busy || !loaded}>
          {busy ? "Saving..." : "Continue"}
        </button>
      }
    >
      <section className="oobe-section">
        <div className="oobe-group">
          <label className="oobe-switch-row" htmlFor="oobe-ping">
            Anonymous update ping
            <input
              id="oobe-ping"
              className="oobe-switch"
              type="checkbox"
              role="switch"
              checked={ping}
              disabled={!loaded?.ping}
              onChange={(e) => setPing(e.target.checked)}
            />
          </label>
        </div>
        <p className="oobe-note">
          {loaded && !loaded.ping
            ? "Couldn't read this setting. Change it later in Settings > Updates."
            : "Once an hour taOS tells taos.my its version, the kind of device and a random install number. Never your name, chats or files."}
        </p>
      </section>

      {loaded?.sharing && (
        <section className="oobe-section">
          <div className="oobe-group">
            <label className="oobe-switch-row" htmlFor="oobe-sharing">
              Share models you download
              <input
                id="oobe-sharing"
                className="oobe-switch"
                type="checkbox"
                role="switch"
                checked={sharing}
                onChange={(e) => setSharing(e.target.checked)}
              />
            </label>
          </div>
          <p className="oobe-note">
            {`Other taOS installs can download models from this one, peer to peer. That uses upload data${
              rate ? ` (up to ${rate})` : ""
            } and shows them this device's IP address.`}
          </p>
        </section>
      )}

      <section className="oobe-section">
        <p className="oobe-section-label">Also online, when you use them</p>
        <ul className="oobe-facts">
          <li>{"Checking for updates contacts GitHub. Turn it off in Settings > Updates."}</li>
          <li>Model and app downloads come from where they are published, such as Hugging Face and GitHub.</li>
          <li>A taOS account, if you sign in to one, keeps your sign-in and linked devices at taos.my.</li>
          <li>
            {loaded && loaded.providers.length > 0
              ? `Cloud models get what you ask them. You've added ${loaded.providers.join(", ")}.`
              : "Cloud models you add get what you ask them."}
          </li>
        </ul>
      </section>

      {error && (
        <p className="oobe-error" role="alert">
          {error}
        </p>
      )}
    </OobePage>
  );
}
