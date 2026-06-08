/**
 * API helpers for the taOS system agent configuration endpoints.
 */

export interface TaosAgentConfig {
  model: string | null;
  permitted_models: string[];
  persona: string;
  key_masked: string | null;
  framework: "opencode";
  system: true;
}

async function _request(url: string, method: string, body?: unknown): Promise<unknown> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const err = await res.json();
      if (err?.error) detail = String(err.error);
      else if (err?.detail) detail = String(err.detail);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

export async function fetchTaosAgentConfig(): Promise<TaosAgentConfig> {
  return _request("/api/taos-agent/config", "GET") as Promise<TaosAgentConfig>;
}

export async function setTaosAgentModel(model: string): Promise<{ model: string }> {
  return _request("/api/taos-agent/settings", "PATCH", { model }) as Promise<{ model: string }>;
}

export async function setTaosAgentPermitted(
  models: string[],
): Promise<{ permitted_models: string[]; key_rescoped: boolean }> {
  return _request("/api/taos-agent/permitted-models", "PUT", { models }) as Promise<{
    permitted_models: string[];
    key_rescoped: boolean;
  }>;
}

export async function setTaosAgentPersona(persona: string): Promise<{ persona: string }> {
  return _request("/api/taos-agent/persona", "PUT", { persona }) as Promise<{ persona: string }>;
}

export interface AttachmentRecord {
  filename: string;
  mime_type: string;
  size: number;
  url: string;
  source?: string;
}

export async function uploadChatAttachment(form: FormData): Promise<AttachmentRecord> {
  const r = await fetch("/api/taos-agent/attachments/upload", {
    method: "POST",
    body: form,
  });
  if (!r.ok) {
    const err = await r.text().catch(() => "upload failed");
    throw new Error(err);
  }
  return r.json() as Promise<AttachmentRecord>;
}

/**
 * Capture one frame from the user's screen via the browser Screen Capture API.
 * Returns a PNG blob.  Throws if the user denies or cancels the permission prompt.
 */
export async function takeChatScreenshot(): Promise<{ blob: Blob; mime_type: string }> {
  const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
  try {
    const video = document.createElement("video");
    video.srcObject = stream;
    video.muted = true;
    await video.play();
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")!.drawImage(video, 0, 0);
    video.pause();
    video.srcObject = null;
    const blob = await new Promise<Blob>((resolve, reject) => {
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("canvas.toBlob failed"))), "image/png");
    });
    return { blob, mime_type: "image/png" };
  } finally {
    stream.getTracks().forEach((t) => t.stop());
  }
}
