// Create clipboard.ts helper for proper clipboard handling with fallback
'use client';

/** Fallback clipboard copy implementation for non-secure contexts. */
export async function copyText(text: string): Promise<boolean> {
  if (!navigator.clipboard) {
    // Fallback for browsers that don't support the Clipboard API
    const textArea = document.createElement("textarea");
    textArea.value = text;
    textArea.style.position = "absolute";
    textArea.style.left = "-999999px";
    document.body.appendChild(textArea);
    textArea.focus();
    textArea.select();
    
    try {
      const successful = document.execCommand("copy");
      return successful;
    } catch (err) {
      console.error("Fallback clipboard copy failed:", err);
      return false;
    } finally {
      document.body.removeChild(textArea);
    }
  }
  
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    console.error("Clipboard copy failed:", err);
    return false;
  }
}
