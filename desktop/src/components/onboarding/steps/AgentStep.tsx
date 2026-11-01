import { useEffect, useState } from "react";
import { Check, ChevronRight, Eye, EyeOff } from "lucide-react";
import { fetchClusterWorkers, workersToAggregated, type AggregatedModel } from "@/lib/models";
import { SETUP_PROVIDERS } from "../agent";
import { OobePage } from "../OobeShell";

/**
 * The taOS Agent changes the system on the user's behalf, so it is offered a
 * capable model first: a cloud key, or a model another taOS device serves.
 * Set Up Later leaves it on the on-device model, which can do less.
 */
export function AgentIntro({
  onCloud,
  onCluster,
  onSkip,
  onBack,
  hasOnDeviceModel,
  busy,
}: {
  onCloud: () => void;
  onCluster: (models: AggregatedModel[]) => void;
  onSkip: () => void;
  onBack: () => void;
  hasOnDeviceModel: boolean;
  busy: boolean;
}) {
  // Read lazily and shown when it lands: a cluster is the exception, and
  // the cloud row must not wait on it.
  const [cluster, setCluster] = useState<AggregatedModel[]>([]);
  useEffect(() => {
    let live = true;
    void fetchClusterWorkers().then((w) => live && setCluster(workersToAggregated(w)));
    return () => { live = false; };
  }, []);

  const rows: { id: string; title: string; detail: string; pick?: () => void }[] = [
    {
      id: "cloud",
      title: "Use a cloud model",
      detail: "Recommended. Add an API key from OpenAI, Anthropic, OpenRouter or another provider.",
      pick: onCloud,
    },
    ...(cluster.length > 0
      ? [{
          id: "cluster",
          title: "Use a model on another taOS device",
          detail: `${cluster.length} ${cluster.length === 1 ? "model" : "models"} on your other devices.`,
          pick: () => onCluster(cluster),
        }]
      : []),
    { id: "free", title: "Free taOS models", detail: "Coming soon." },
  ];

  return (
    <OobePage
      title="Choose the taOS Agent's model"
      subtitle="The taOS Agent changes settings, installs apps and manages files for you, so it works best with a capable model."
      onBack={busy ? undefined : onBack}
      onSubmit={() => {}}
      footer={
        <button type="button" className="oobe-secondary" onClick={onSkip} disabled={busy} aria-busy={busy || undefined}>
          Set Up Later
        </button>
      }
    >
      <div className="oobe-group">
        {rows.map((r) => (
          <button
            key={r.id}
            type="button"
            className="oobe-choice"
            onClick={r.pick}
            disabled={!r.pick || busy}
          >
            <span className="oobe-choice-text">
              <span className="oobe-choice-title">{r.title}</span>
              <span className="oobe-choice-detail">{r.detail}</span>
            </span>
            {r.pick && <ChevronRight size={20} aria-hidden="true" className="oobe-choice-chevron" />}
          </button>
        ))}
      </div>
      <p className="oobe-note">
        {hasOnDeviceModel
          ? "Until then it uses the model on this device, which can do less."
          : "Until then the taOS Agent has no model. Add one any time in Settings."}
      </p>
    </OobePage>
  );
}

export function AgentCloud({
  type,
  apiKey,
  busy,
  error,
  onType,
  onKey,
  onBack,
  onConnect,
}: {
  type: string;
  apiKey: string;
  busy: boolean;
  error: string;
  onType: (type: string) => void;
  onKey: (key: string) => void;
  onBack: () => void;
  onConnect: () => void;
}) {
  const [shown, setShown] = useState(false);
  const meta = SETUP_PROVIDERS.find((p) => p.type === type);
  const valid = apiKey.trim().length > 0;
  return (
    <OobePage
      title="Add a cloud model"
      subtitle="Your key is stored on this device and used only to reach the provider you pick."
      onBack={busy ? undefined : onBack}
      onSubmit={() => valid && !busy && onConnect()}
      footer={
        <button type="submit" className="oobe-primary" disabled={!valid || busy} aria-busy={busy || undefined}>
          {busy ? "Connecting..." : "Connect"}
        </button>
      }
    >
      <div className="oobe-section">
        <p className="oobe-section-label" id="oobe-provider-label">Provider</p>
        <div className="oobe-group" role="radiogroup" aria-labelledby="oobe-provider-label">
          {SETUP_PROVIDERS.map((p) => (
            <button
              key={p.type}
              type="button"
              role="radio"
              aria-checked={type === p.type}
              className="oobe-choice"
              onClick={() => onType(p.type)}
              disabled={busy}
            >
              <span className="oobe-choice-text">
                <span className="oobe-choice-title">{p.label}</span>
                <span className="oobe-choice-detail">{p.description}</span>
              </span>
              <Check
                size={20}
                aria-hidden="true"
                className="oobe-choice-check"
                style={{ visibility: type === p.type ? "visible" : "hidden" }}
              />
            </button>
          ))}
        </div>
      </div>
      <div className="oobe-group">
        <div className="oobe-row">
          <label htmlFor="oobe-api-key">API key</label>
          <input
            id="oobe-api-key"
            className="oobe-input"
            type={shown ? "text" : "password"}
            value={apiKey}
            onChange={(e) => onKey(e.target.value)}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder={meta?.keyPlaceholder ?? "Required"}
            disabled={busy}
          />
          <button
            type="button"
            className="oobe-reveal"
            onClick={() => setShown((s) => !s)}
            aria-label={shown ? "Hide API key" : "Show API key"}
          >
            {shown ? <EyeOff size={20} aria-hidden="true" /> : <Eye size={20} aria-hidden="true" />}
          </button>
        </div>
      </div>
      {error && <p className="oobe-error" role="alert">{error}</p>}
    </OobePage>
  );
}

const SEARCH_FROM = 8;
const SHOW_AT_MOST = 40;

/** One model for the taOS Agent, from a provider's list or the cluster's. */
export function AgentModelPick({
  title,
  subtitle,
  models,
  busy,
  error,
  onBack,
  onPick,
}: {
  title: string;
  subtitle: string;
  models: { id: string; detail?: string }[];
  busy: boolean;
  error: string;
  onBack: () => void;
  onPick: (id: string) => void;
}) {
  const [picked, setPicked] = useState(models[0]?.id ?? "");
  const [query, setQuery] = useState("");
  const searchable = models.length > SEARCH_FROM;
  const q = query.trim().toLowerCase();
  const matches = q ? models.filter((m) => m.id.toLowerCase().includes(q)) : models;
  const shown = matches.slice(0, SHOW_AT_MOST);
  // A pick the filter has hidden is not sent: Continue would save a model
  // the user can no longer see.
  const ready = shown.some((m) => m.id === picked);
  return (
    <OobePage
      title={title}
      subtitle={subtitle}
      onBack={busy ? undefined : onBack}
      onSubmit={() => ready && !busy && onPick(picked)}
      footer={
        <button type="submit" className="oobe-primary" disabled={!ready || busy} aria-busy={busy || undefined}>
          {busy ? "Saving..." : "Continue"}
        </button>
      }
    >
      {searchable && (
        <div className="oobe-group">
          <div className="oobe-row oobe-row-bare">
            <input
              className="oobe-input"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Filter models"
              placeholder={`Filter ${models.length} models`}
              autoCapitalize="none"
              spellCheck={false}
              disabled={busy}
            />
          </div>
        </div>
      )}
      <div className="oobe-group" role="radiogroup" aria-label="Models" hidden={shown.length === 0}>
        {shown.map((m) => (
          <button
            key={m.id}
            type="button"
            role="radio"
            aria-checked={picked === m.id}
            className="oobe-choice"
            onClick={() => setPicked(m.id)}
            disabled={busy}
          >
            <span className="oobe-choice-text">
              <span className="oobe-choice-title oobe-choice-id">{m.id}</span>
              {m.detail && <span className="oobe-choice-detail">{m.detail}</span>}
            </span>
            <Check
              size={20}
              aria-hidden="true"
              className="oobe-choice-check"
              style={{ visibility: picked === m.id ? "visible" : "hidden" }}
            />
          </button>
        ))}
      </div>
      {matches.length === 0 ? (
        <p className="oobe-note">No model matches "{query.trim()}".</p>
      ) : (
        matches.length > shown.length && (
          <p className="oobe-note">Showing {shown.length} of {matches.length}. Type to narrow the list.</p>
        )
      )}
      {error && <p className="oobe-error" role="alert">{error}</p>}
    </OobePage>
  );
}
