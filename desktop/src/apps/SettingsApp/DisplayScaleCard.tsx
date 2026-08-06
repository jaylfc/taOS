import { Card } from "@/components/ui";
import { useDisplayStore, SCALE_STEPS } from "@/stores/display-store";

// macOS "Displays > Scaled" style chooser.
//
// Native radios rather than styled buttons: a radiogroup built from buttons has
// to reimplement arrow-key navigation, roving tabindex and the checked
// announcement, and usually gets one of them wrong. The inputs are visually
// hidden but still focusable, so keyboard and screen-reader behaviour is the
// platform's rather than ours.

export function DisplayScaleCard() {
  const uiScale = useDisplayStore((s) => s.uiScale);
  const setUiScale = useDisplayStore((s) => s.setUiScale);

  return (
    <Card className="p-4">
      <p className="text-sm font-medium mb-1">Display Scale</p>
      <p className="text-xs text-muted-foreground mb-3">
        Make everything smaller to fit more on screen. Saved on this device only.
      </p>

      <fieldset>
        <legend className="sr-only">Display scale</legend>
        {/* Three across on phones, all five in a row from `sm` up. Five equal
            boxes inside a phone-width Settings pane leave roughly 55px each,
            which wraps "125%" onto two lines and squashes the end captions. */}
        <div className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {SCALE_STEPS.map((step) => {
            const selected = step.value === uiScale;
            return (
              <label
                key={step.value}
                className={[
                  "cursor-pointer rounded-md border px-2 py-3 text-center transition-colors",
                  "focus-within:ring-2 focus-within:ring-offset-1",
                  selected ? "border-primary bg-primary/10" : "border-border hover:bg-muted/50",
                ].join(" ")}
              >
                <input
                  type="radio"
                  name="taos-display-scale"
                  className="sr-only"
                  value={step.value}
                  checked={selected}
                  onChange={() => setUiScale(step.value)}
                />
                {/* The percentage is the accessible name; the caption below is
                    decorative and would otherwise be read as part of it. */}
                <span className="block text-sm font-medium">{step.label}</span>
                {step.caption ? (
                  <span aria-hidden="true" className="mt-1 block text-[11px] text-muted-foreground">
                    {step.caption}
                  </span>
                ) : null}
              </label>
            );
          })}
        </div>
      </fieldset>

      <p aria-live="polite" className="sr-only">
        Display scale {Math.round(uiScale * 100)} percent
      </p>
    </Card>
  );
}
