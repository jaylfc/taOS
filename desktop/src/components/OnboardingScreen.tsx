import { Oobe, type OobeProps } from "./onboarding/Oobe";

/**
 * First-run setup (no invite props) or invite completion (invitedUsername).
 *
 * First run walks the setup screens in onboarding/; creating the local
 * account signs this browser in, and the screens after it are optional.
 * Invite completion asks only for a name and password, then POSTs
 * /auth/complete instead of /auth/setup.
 */
export function OnboardingScreen(props: OobeProps) {
  return <Oobe {...props} />;
}
