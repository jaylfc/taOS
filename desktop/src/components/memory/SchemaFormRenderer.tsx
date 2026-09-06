import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Label } from "@/components/ui/label";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  default?: unknown;
}

interface SchemaFormRendererProps {
  schema: Record<string, SchemaProperty>;
  values: Record<string, any>;
  onChange: (key: string, value: any) => void;
}

/* ------------------------------------------------------------------ */
/*  SchemaFormRenderer                                                 */
/* ------------------------------------------------------------------ */

export function SchemaFormRenderer({ schema, values, onChange }: SchemaFormRendererProps) {
  if (!schema || Object.keys(schema).length === 0) {
    return (
      <p className="text-xs text-shell-text-tertiary py-4 text-center">No settings available.</p>
    );
  }

  return (
    <div className="space-y-4">
      {Object.entries(schema).map(([key, prop]) => {
        const label = prop.title ?? key;
        const value = values[key] ?? prop.default ?? '';
        const fieldId = `schema-field-${key}`;

        if (prop.type === 'boolean') {
          return (
            <div key={key} className="flex items-center justify-between gap-4 py-1">
              <div className="flex flex-col gap-0.5 min-w-0">
                <Label htmlFor={fieldId} className="text-sm font-medium text-shell-text cursor-pointer">
                  {label}
                </Label>
                {prop.description && (
                  <p className="text-xs text-shell-text-tertiary">{prop.description}</p>
                )}
              </div>
              <Switch
                id={fieldId}
                checked={Boolean(value)}
                onCheckedChange={(checked) => onChange(key, checked)}
                aria-label={label}
              />
            </div>
          );
        }

        if (prop.type === 'string' && prop.enum) {
          return (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={fieldId} className="text-sm font-medium text-shell-text">
                {label}
              </Label>
              {prop.description && (
                <p className="text-xs text-shell-text-tertiary">{prop.description}</p>
              )}
              <select
                id={fieldId}
                value={String(value)}
                onChange={(e) => onChange(key, e.target.value)}
                aria-label={label}
                className="w-full rounded-md border border-white/10 bg-white/[0.04] px-3 py-1.5 text-sm text-shell-text focus:outline-none focus:ring-1 focus:ring-accent"
              >
                {prop.enum.map((opt) => (
                  <option key={opt} value={opt} className="bg-gray-900">
                    {opt}
                  </option>
                ))}
              </select>
            </div>
          );
        }

        if (prop.type === 'number' || prop.type === 'integer') {
          return (
            <div key={key} className="flex flex-col gap-1.5">
              <Label htmlFor={fieldId} className="text-sm font-medium text-shell-text">
                {label}
              </Label>
              {prop.description && (
                <p className="text-xs text-shell-text-tertiary">{prop.description}</p>
              )}
              <Input
                id={fieldId}
                type="number"
                value={value === '' || value === null || value === undefined ? '' : Number(value)}
                min={prop.minimum}
                max={prop.maximum}
                onChange={(e) => onChange(key, e.target.value === '' ? '' : Number(e.target.value))}
                aria-label={label}
                className="h-8"
              />
            </div>
          );
        }

        // default: string text input
        return (
          <div key={key} className="flex flex-col gap-1.5">
            <Label htmlFor={fieldId} className="text-sm font-medium text-shell-text">
              {label}
            </Label>
            {prop.description && (
              <p className="text-xs text-shell-text-tertiary">{prop.description}</p>
            )}
            <Input
              id={fieldId}
              type="text"
              value={String(value)}
              onChange={(e) => onChange(key, e.target.value)}
              aria-label={label}
              className="h-8"
            />
          </div>
        );
      })}
    </div>
  );
}
