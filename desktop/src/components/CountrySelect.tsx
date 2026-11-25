import { useMemo } from "react";

/** ISO 3166-1 alpha-2 codes with a display name in the person's language.
 *  Built from Intl so no list has to be kept by hand; codes the runtime has no
 *  name for are dropped. */
export function countryOptions(locale?: string): Array<{ code: string; name: string }> {
  let names: Intl.DisplayNames | null = null;
  try {
    names = new Intl.DisplayNames(locale ? [locale] : undefined, { type: "region" });
  } catch {
    return [];
  }
  const out: Array<{ code: string; name: string }> = [];
  for (let a = 65; a <= 90; a++) {
    for (let b = 65; b <= 90; b++) {
      const code = String.fromCharCode(a, b);
      let name: string | undefined;
      try { name = names.of(code); } catch { name = undefined; }
      if (name && name !== code) out.push({ code, name });
    }
  }
  return out.sort((x, y) => x.name.localeCompare(y.name));
}

interface Props {
  value: string;
  onChange: (code: string) => void;
  id?: string;
  disabled?: boolean;
  "aria-label"?: string;
}

/** Native select (works with touch pickers on a phone as well as a desktop). */
export function CountrySelect({ value, onChange, id, disabled, ...rest }: Props) {
  const options = useMemo(() => countryOptions(), []);
  return (
    <select
      id={id}
      value={value}
      disabled={disabled}
      aria-label={rest["aria-label"] ?? "Country"}
      onChange={(e) => onChange(e.target.value)}
      className="w-full min-h-[44px] rounded-lg bg-shell-surface border border-white/10 px-3 text-sm text-shell-text"
    >
      <option value="">Not set</option>
      {options.map((o) => (
        <option key={o.code} value={o.code}>{o.name}</option>
      ))}
    </select>
  );
}

export default CountrySelect;
