/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface Chapter {
  title: string;
  start_time: number;
  end_time: number;
}

export interface YouTubeMetadata {
  video_id: string;
  channel: string;
  views: number;
  likes: number;
  duration: number;
  upload_date: string;
  chapters: Chapter[];
  transcript_segments: TranscriptSegment[];
}

export interface DownloadStatus {
  status: "idle" | "downloading" | "complete" | "error";
  file_size?: string;
  path?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

async function fetchJson<T>(url: string, fallback: T, init?: RequestInit): Promise<T> {
  try {
    const headers = new Headers(init?.headers);
    headers.set("Accept", "application/json");
    const res = await fetch(url, { ...init, headers });
    if (!res.ok) return fallback;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return fallback;
    return await res.json();
  } catch {
    return fallback;
  }
}

async function postJsonRaw<T>(
  url: string,
  body: unknown,
): Promise<T | null> {
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ */
/*  API functions                                                      */
/* ------------------------------------------------------------------ */

export async function ingestVideo(url: string): Promise<{ id: string; status: string } | null> {
  return postJsonRaw<{ id: string; status: string }>("/api/youtube/ingest", { url });
}

export async function downloadVideo(
  itemId: string,
  quality: string,
): Promise<{ status: string } | null> {
  return postJsonRaw<{ status: string }>("/api/youtube/download", { item_id: itemId, quality });
}

export async function getDownloadStatus(itemId: string): Promise<DownloadStatus> {
  return fetchJson<DownloadStatus>(
    `/api/youtube/download-status/${encodeURIComponent(itemId)}`,
    { status: "idle" },
  );
}

export async function getTranscript(itemId: string): Promise<TranscriptSegment[]> {
  const data = await fetchJson<{ segments: TranscriptSegment[] }>(
    `/api/youtube/transcript/${encodeURIComponent(itemId)}`,
    { segments: [] },
  );
  return Array.isArray(data.segments) ? data.segments : [];
}

/* ------------------------------------------------------------------ */
/*  Client-side helpers                                                */
/* ------------------------------------------------------------------ */

export function formatTimestamp(seconds: number): string {
  const totalSecs = Math.floor(seconds);
  const h = Math.floor(totalSecs / 3600);
  const m = Math.floor((totalSecs % 3600) / 60);
  const s = totalSecs % 60;
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  if (h > 0) {
    return `${h}:${mm}:${ss}`;
  }
  return `${mm}:${ss}`;
}

