/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface RedditPost {
  id: string;
  subreddit: string;
  title: string;
  author: string;
  selftext: string;
  score: number;
  upvote_ratio: number;
  num_comments: number;
  created_utc: number;
  url: string;
  permalink: string;
  flair: string;
  is_self: boolean;
}

export interface RedditComment {
  id: string;
  author: string;
  body: string;
  score: number;
  created_utc: number;
  depth: number;
  parent_id: string;
  replies: RedditComment[];
  edited: boolean;
  distinguished: string | null;
}

export interface RedditThread {
  post: RedditPost;
  comments: RedditComment[];
}

export interface RedditListing {
  posts: RedditPost[];
  after: string | null;
}

export interface RedditAuthStatus {
  authenticated: boolean;
  username?: string;
}

/* ------------------------------------------------------------------ */
/*  Helpers                                                            */
/* ------------------------------------------------------------------ */

const EMPTY_LISTING: RedditListing = { posts: [], after: null };

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

/* ------------------------------------------------------------------ */
/*  API functions                                                      */
/* ------------------------------------------------------------------ */

/**
 * Fetch a full Reddit thread (post + comments) by URL.
 */
export async function fetchThread(url: string): Promise<RedditThread | null> {
  const qs = new URLSearchParams({ url });
  try {
    const res = await fetch(`/api/reddit/thread?${qs}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}

/**
 * Fetch a listing of posts from a subreddit.
 */
export async function fetchSubreddit(
  name: string,
  sort: string = "hot",
  after?: string,
): Promise<RedditListing> {
  const qs = new URLSearchParams({ name, sort });
  if (after) qs.set("after", after);
  const data = await fetchJson<RedditListing>(
    `/api/reddit/subreddit?${qs}`,
    { ...EMPTY_LISTING },
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/**
 * Search Reddit, optionally restricted to a subreddit.
 */
export async function searchReddit(
  query: string,
  subreddit?: string,
): Promise<RedditListing> {
  const qs = new URLSearchParams({ q: query });
  if (subreddit) qs.set("subreddit", subreddit);
  const data = await fetchJson<RedditListing>(
    `/api/reddit/search?${qs}`,
    { ...EMPTY_LISTING },
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/**
 * Fetch the authenticated user's saved posts.
 */
export async function fetchSaved(after?: string): Promise<RedditListing> {
  const qs = new URLSearchParams();
  if (after) qs.set("after", after);
  const query = qs.toString();
  const data = await fetchJson<RedditListing>(
    `/api/reddit/saved${query ? `?${query}` : ""}`,
    { ...EMPTY_LISTING },
  );
  return {
    posts: Array.isArray(data.posts) ? data.posts : [],
    after: data.after ?? null,
  };
}

/**
 * Get the Reddit OAuth authentication status.
 */
export async function getAuthStatus(): Promise<RedditAuthStatus> {
  return fetchJson<RedditAuthStatus>(
    "/api/reddit/auth/status",
    { authenticated: false },
  );
}

/**
 * Save a Reddit post URL to the Knowledge Library via the ingest endpoint.
 */
export async function saveToLibrary(
  url: string,
  title?: string,
): Promise<{ id: string; status: string } | null> {
  try {
    const res = await fetch("/api/knowledge/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({
        url,
        title: title ?? "",
        text: "",
        categories: [],
        source: "reddit-client",
      }),
    });
    if (!res.ok) return null;
    const ct = res.headers.get("content-type") ?? "";
    if (!ct.includes("application/json")) return null;
    return await res.json();
  } catch {
    return null;
  }
}
