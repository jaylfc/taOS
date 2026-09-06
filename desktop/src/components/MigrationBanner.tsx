export function MigrationBanner({ agent, onDismiss, onAddPersona }: {
  agent: { migrated_to_v2_personas?: boolean };
  onDismiss: () => void;
  onAddPersona: () => void;
}) {
  if (agent?.migrated_to_v2_personas) return null;
  return (
    <div className="bg-yellow-950/30 border border-yellow-800 rounded px-3 py-2 flex items-center justify-between mb-3">
      <span className="text-sm">
        Memory upgraded — this agent now knows how to use taOSmd. Add a persona to give it character.
      </span>
      <div className="flex gap-2">
        <button onClick={onAddPersona} className="text-blue-400 text-sm">Add persona →</button>
        <button onClick={onDismiss} className="opacity-60 text-sm">Dismiss</button>
      </div>
    </div>
  );
}
