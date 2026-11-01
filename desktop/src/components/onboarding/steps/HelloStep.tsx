import { OobePage } from "../OobeShell";

export function HelloStep({ onNext }: { onNext: () => void }) {
  return (
    <OobePage
      mark
      title="Welcome to taOS"
      subtitle="Your own AI, running on hardware you own. Setup takes about five minutes."
      onSubmit={onNext}
      footer={
        <button type="submit" className="oobe-primary" autoFocus>
          Get started
        </button>
      }
    />
  );
}
