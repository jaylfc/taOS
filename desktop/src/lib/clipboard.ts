// Copies text to the clipboard, with a fallback for non-secure origins
// (plain HTTP on a LAN / Tailscale IP) where navigator.clipboard is
// unavailable. Returns true if the copy succeeded, false otherwise.
export async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // fall through to execCommand fallback
    }
  }
  return fallbackCopy(text);
}

function fallbackCopy(text: string): boolean {
  if (typeof document.execCommand !== "function") return false;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.setAttribute("readonly", "");
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  let ok = false;
  const previouslyFocused = document.activeElement;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  } finally {
    document.body.removeChild(textarea);
    if (previouslyFocused instanceof HTMLElement) {
      previouslyFocused.focus();
    }
  }
  return ok;
}
