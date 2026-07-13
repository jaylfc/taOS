import { useState, useEffect, useCallback, useRef } from "react";
import { Users, Globe, Send, Trash2, Image as ImageIcon, AlertCircle } from "lucide-react";
import { Button, Textarea } from "@/components/ui";

// ---- Types ----

interface HubPost {
  type: "post";
  author: string;
  seq: number;
  prev: string | null;
  created_at: string;
  visibility: "public" | "circle";
  body: { text: string; format: string };
  attachments: { blob: string; size: number; mime: string }[];
  sig: string;
  hash?: string;
}

// Visibility tiers (design "Privacy tiers"). Slice 7 adds real friends-only
// encryption; until then "circle" is enforced later by serve-authorization and
// the composer labels it loudly. Default is friends-only, matching the design's
// "a loud friends-only/public switch defaulting to friends-only".
type Visibility = "circle" | "public";

const VISIBILITY_OPTIONS: { value: Visibility; label: string; icon: typeof Users; hint: string }[] = [
  { value: "circle", label: "Friends-only", icon: Users, hint: "Only friends can read this (encrypted in a later slice)" },
  { value: "public", label: "Public", icon: Globe, hint: "Anyone with the link can read this" },
];

function relativeTime(ts: string): string {
  const diff = Date.now() - Date.parse(ts);
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

// ---- Composer ----

function Composer({ onPosted }: { onPosted: () => void }) {
  const [text, setText] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("circle");
  const [attachments, setAttachments] = useState<{ data: string; mime: string; name: string }[]>([]);
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  function onPickFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const files = Array.from(e.target.files ?? []);
    for (const f of files) {
      const reader = new FileReader();
      reader.onload = () => {
        setAttachments((prev) => [
          ...prev,
          { data: String(reader.result), mime: f.type || "image/png", name: f.name },
        ]);
      };
      reader.readAsDataURL(f);
    }
    e.target.value = "";
  }

  function removeAttachment(idx: number) {
    setAttachments((prev) => prev.filter((_, i) => i !== idx));
  }

  async function post() {
    if (!text.trim() && attachments.length === 0) return;
    setPosting(true);
    setError(null);
    try {
      const r = await fetch("/api/hub/posts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          visibility,
          text: text.trim(),
          attachments: attachments.map((a) => ({ data: a.data, mime: a.mime })),
        }),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        throw new Error(typeof d?.error === "string" ? d.error : "Could not publish post.");
      }
      setText("");
      setAttachments([]);
      onPosted();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not publish post.");
    } finally {
      setPosting(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-shell-border bg-shell-surface p-4">
      <div className="flex items-center gap-2">
        <span className="text-xs font-medium text-shell-text-secondary">New post</span>
        <span className="text-[11px] text-shell-text-tertiary">
          posts live on your node, signed by your key
        </span>
      </div>

      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="What's on your mind?"
        rows={3}
        maxLength={20000}
        aria-label="Post text"
        className="resize-none border-shell-border bg-shell-bg-deep text-shell-text placeholder:text-shell-text-tertiary"
      />

      {/* Visibility switch -- loud, defaults to friends-only */}
      <div
        className="flex rounded-lg border border-shell-border bg-shell-bg-deep p-0.5"
        role="group"
        aria-label="Post visibility"
      >
        {VISIBILITY_OPTIONS.map((opt) => {
          const Icon = opt.icon;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => setVisibility(opt.value)}
              aria-pressed={visibility === opt.value}
              title={opt.hint}
              className={[
                "flex flex-1 items-center justify-center gap-1.5 rounded-md py-1.5 text-xs font-medium transition-colors",
                visibility === opt.value
                  ? "bg-shell-bg text-shell-text shadow-sm"
                  : "text-shell-text-secondary hover:text-shell-text",
              ].join(" ")}
            >
              <Icon size={13} className="shrink-0" />
              {opt.label}
            </button>
          );
        })}
      </div>
      <p className="text-[11px] text-shell-text-tertiary">
        {VISIBILITY_OPTIONS.find((o) => o.value === visibility)?.hint}
      </p>

      {/* Attachments */}
      {attachments.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {attachments.map((a, i) => (
            <li
              key={i}
              className="flex items-center gap-2 rounded-lg border border-shell-border bg-shell-bg-deep px-3 py-2"
            >
              <ImageIcon size={14} className="shrink-0 text-accent" />
              <span className="min-w-0 flex-1 truncate text-xs text-shell-text-secondary">{a.name}</span>
              <button
                type="button"
                onClick={() => removeAttachment(i)}
                aria-label={`Remove attachment ${a.name}`}
                className="text-shell-text-tertiary transition-colors hover:text-red-400"
              >
                <Trash2 size={13} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && (
        <div
          className="flex items-center gap-2 rounded-lg border border-red-500/20 bg-red-500/10 px-3 py-2 text-xs text-red-400"
          role="alert"
        >
          <AlertCircle size={13} className="shrink-0" />
          {error}
        </div>
      )}

      <div className="flex items-center justify-between">
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          onChange={onPickFiles}
          className="hidden"
        />
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => fileRef.current?.click()}
          aria-label="Attach image"
        >
          <ImageIcon size={13} />
          Attach
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={post}
          disabled={posting || (!text.trim() && attachments.length === 0)}
          aria-label="Publish post"
        >
          <Send size={13} />
          {posting ? "Publishing..." : "Publish"}
        </Button>
      </div>
    </div>
  );
}

// ---- Timeline ----

function Timeline({ identityReady }: { identityReady: boolean }) {
  const [posts, setPosts] = useState<HubPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [, setError] = useState<string | null>(null);
  const reqRef = useRef(0);

  const load = useCallback(async () => {
    const my = ++reqRef.current;
    try {
      const r = await fetch("/api/hub/timeline");
      if (!r.ok) throw new Error("Could not load timeline.");
      const data = await r.json();
      if (reqRef.current !== my) return;
      if (data.state === "no-identity") {
        setPosts([]);
        setError(null);
      } else {
        setPosts(Array.isArray(data.posts) ? (data.posts as HubPost[]) : []);
        setError(null);
      }
    } catch (e) {
      if (reqRef.current === my)
        setError(e instanceof Error ? e.message : "Could not load timeline.");
    } finally {
      if (reqRef.current === my) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (identityReady) load();
  }, [identityReady, load]);

  async function remove(post: HubPost) {
    const id = post.hash;
    if (!id) return;
    try {
      const r = await fetch(`/api/hub/posts/${id}/delete`, { method: "POST" });
      if (!r.ok) throw new Error("Could not delete post.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not delete post.");
    }
  }

  if (loading) {
    return <p className="text-sm text-shell-text-tertiary">Loading timeline...</p>;
  }

  if (!identityReady) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-center">
        <Users size={28} className="text-shell-text-tertiary" />
        <p className="text-sm text-shell-text-secondary">
          No hub identity yet. Publish a post to mint your key.
        </p>
      </div>
    );
  }

  if (posts.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-center">
        <Globe size={28} className="text-shell-text-tertiary" />
        <p className="text-sm text-shell-text-secondary">No posts yet. Say something.</p>
      </div>
    );
  }

  return (
        <ul className="flex flex-col gap-3" aria-label="Your posts">
          {posts.map((post) => (
            <li
              key={post.hash ?? post.seq}
          className="flex flex-col gap-2 rounded-xl border border-shell-border bg-shell-surface p-4"
        >
          <div className="flex items-center justify-between gap-2">
            <span
              className={[
                "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
                post.visibility === "circle"
                  ? "bg-accent/10 text-accent"
                  : "bg-shell-surface text-shell-text-secondary",
              ].join(" ")}
            >
              {post.visibility === "circle" ? <Users size={11} /> : <Globe size={11} />}
              {post.visibility === "circle" ? "Friends-only" : "Public"}
            </span>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-shell-text-tertiary">
                {relativeTime(post.created_at)}
              </span>
              <button
                type="button"
                onClick={() => remove(post)}
                aria-label="Delete post"
                className="text-shell-text-tertiary transition-colors hover:text-red-400"
              >
                <Trash2 size={13} />
              </button>
            </div>
          </div>

          <p className="whitespace-pre-wrap text-sm text-shell-text">{post.body.text}</p>

          {post.attachments.length > 0 && (
            <ul className="flex flex-col gap-1.5">
              {post.attachments.map((att, i) => (
                <li
                  key={i}
                  className="flex items-center gap-2 rounded-lg border border-shell-border bg-shell-bg-deep px-3 py-2 text-xs text-shell-text-secondary"
                >
                  <ImageIcon size={13} className="shrink-0 text-accent" />
                  <span className="font-mono truncate">{att.blob.slice(0, 12)}…</span>
                  <span className="ml-auto text-shell-text-tertiary">{formatBytes(att.size)}</span>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

// ---- Main app ----

export function HubApp({ windowId: _windowId }: { windowId: string }) {
  const [identityReady, setIdentityReady] = useState(false);
  const [postsVersion, setPostsVersion] = useState(0);

  useEffect(() => {
    // Publishing a post mints the identity (routes call load_or_create), so the
    // only "no identity" case is before the first post. Treat the app as ready
    // and let the timeline show its own state.
    setIdentityReady(true);
  }, []);

  return (
    <div className="flex h-full flex-col overflow-hidden bg-shell-bg">
      <div className="flex items-center gap-2 border-b border-shell-border px-4 py-4">
        <Users size={17} className="text-accent" />
        <h1 className="flex-1 text-base font-semibold text-shell-text">Hub</h1>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-4">
        <div className="mx-auto flex max-w-2xl flex-col gap-4">
          <Composer onPosted={() => setPostsVersion((v) => v + 1)} />
          <Timeline key={postsVersion} identityReady={identityReady} />
        </div>
      </div>
    </div>
  );
}

export default HubApp;
