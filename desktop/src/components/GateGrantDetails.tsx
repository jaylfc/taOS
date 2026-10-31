// tsk-mul5pa: a gate decision's question is free text from whoever raised it,
// while the grant it mints on approval is driven by its metadata. Show the
// approver the grant-bearing metadata fields so they can see what they are
// granting. Renders nothing for a decision that is not a server-owned gate.

type GateFields = { title: string; fields: Array<[key: string, label: string]> };

const GATE_KINDS: Record<string, GateFields> = {
  execution_gate: {
    title: "Lets an agent run an action",
    fields: [["agent_name", "Agent"], ["action_class", "Action class"], ["tool", "Tool"]],
  },
  delegation_gate: {
    title: "Delegates a task to another agent",
    fields: [["from_agent", "From"], ["to_agent", "To"], ["task_title", "Task"], ["task_id", "Task id"]],
  },
  device_pairing: {
    title: "Pairs a device with this server",
    fields: [["pair_request_id", "Pair request"], ["server_cert_fingerprint", "Server certificate"]],
  },
  app_grant: {
    title: "Grants an app permissions",
    fields: [["app_id", "App"], ["capabilities", "Capabilities"]],
  },
  project_create: {
    title: "Creates a project",
    fields: [["requested_name", "Name"], ["requested_slug", "Slug"], ["from_agent", "Requested by"]],
  },
  collab_delegation_gate: {
    title: "Invites a collaborator to a project",
    fields: [
      ["contact_id", "Contact"],
      ["project_id", "Project"],
      ["granted_scopes", "Scopes"],
      ["elevated_scopes", "Elevated scopes"],
    ],
  },
};

function display(value: unknown): string {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value === null || value === undefined) return "";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

export function GateGrantDetails({ metadata }: { metadata?: Record<string, unknown> | null }) {
  const kind = typeof metadata?.kind === "string" ? metadata.kind : "";
  // Own keys only: a caller-chosen kind like "toString" must not resolve to an
  // inherited Object property.
  const gate = Object.prototype.hasOwnProperty.call(GATE_KINDS, kind) ? GATE_KINDS[kind] : undefined;
  if (!metadata || !gate) return null;
  const rows = gate.fields
    .map(([key, label]) => [label, display(metadata[key])] as const)
    .filter(([, value]) => value !== "");

  return (
    <div
      role="group"
      aria-label="What approving grants"
      className="flex flex-col gap-1 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs"
    >
      <span className="font-medium text-shell-text">Approving: {gate.title}</span>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-shell-text-tertiary">{label}</dt>
            <dd className="break-all font-mono text-shell-text-secondary">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
