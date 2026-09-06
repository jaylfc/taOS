import { useState } from "react";
import { ChevronLeft, Clock, Tag, Sparkles, ChevronDown, ChevronRight, FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

export interface Session {
  id: number;
  date: string;
  topic: string;
  description?: string;
  start_time?: string;
  end_time?: string;
  category?: string;
  sub_sessions?: Session[];
  crystal_narrative?: string;
  raw_lines?: string[];
}

interface SessionDetailProps {
  session: Session;
  onBack: () => void;
}

/* ------------------------------------------------------------------ */
/*  SessionDetail                                                      */
/* ------------------------------------------------------------------ */

export function SessionDetail({ session, onBack }: SessionDetailProps) {
  const [showRaw, setShowRaw] = useState(false);
  const [expandedSubs, setExpandedSubs] = useState<Set<number>>(new Set());

  const toggleSub = (id: number) => {
    setExpandedSubs((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const timeRange =
    session.start_time && session.end_time
      ? `${session.start_time} – ${session.end_time}`
      : session.start_time ?? session.end_time ?? null;

  return (
    <article className="flex flex-col gap-4 p-4 overflow-auto h-full" aria-label={`Session detail: ${session.topic}`}>
      {/* Back button */}
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onBack}
          className="h-7 px-2 gap-1.5 text-xs text-shell-text-secondary"
          aria-label="Back to session list"
        >
          <ChevronLeft size={13} aria-hidden="true" />
          Back
        </Button>
      </div>

      {/* Header */}
      <div className="flex flex-col gap-2">
        <h2 className="text-base font-semibold text-shell-text leading-snug">{session.topic}</h2>
        <div className="flex flex-wrap items-center gap-3 text-xs text-shell-text-tertiary">
          <span>{session.date}</span>
          {timeRange && (
            <span className="flex items-center gap-1">
              <Clock size={11} aria-hidden="true" />
              {timeRange}
            </span>
          )}
          {session.category && (
            <span className="flex items-center gap-1 px-2 py-0.5 rounded-full bg-accent/15 text-accent border border-accent/20">
              <Tag size={10} aria-hidden="true" />
              {session.category}
            </span>
          )}
        </div>
      </div>

      {/* Description */}
      {session.description && (
        <p className="text-sm text-shell-text-secondary leading-relaxed">{session.description}</p>
      )}

      {/* Crystal narrative */}
      {session.crystal_narrative && (
        <Card className="bg-pink-500/[0.06] border-pink-500/20">
          <CardContent className="p-4 flex flex-col gap-2">
            <div className="flex items-center gap-1.5 text-pink-400 text-xs font-medium uppercase tracking-wider">
              <Sparkles size={13} aria-hidden="true" />
              Crystal Narrative
            </div>
            <p className="text-sm text-shell-text-secondary leading-relaxed whitespace-pre-wrap">
              {session.crystal_narrative}
            </p>
          </CardContent>
        </Card>
      )}

      {/* Sub-sessions */}
      {Array.isArray(session.sub_sessions) && session.sub_sessions.length > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="text-xs font-medium text-shell-text-tertiary uppercase tracking-wider">
            Sub-sessions ({session.sub_sessions.length})
          </h3>
          <div className="space-y-1.5" role="list" aria-label="Sub-sessions">
            {session.sub_sessions.map((sub) => {
              const isOpen = expandedSubs.has(sub.id);
              return (
                <div
                  key={sub.id}
                  className="rounded-md border border-white/8 bg-white/[0.02] overflow-hidden"
                  role="listitem"
                >
                  <button
                    type="button"
                    onClick={() => toggleSub(sub.id)}
                    aria-expanded={isOpen}
                    aria-controls={`sub-${sub.id}`}
                    className="w-full flex items-center justify-between gap-2 px-3 py-2.5 text-left hover:bg-white/[0.03] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent"
                  >
                    <span className="text-sm text-shell-text truncate">{sub.topic}</span>
                    {isOpen ? (
                      <ChevronDown size={13} className="shrink-0 text-shell-text-tertiary" aria-hidden="true" />
                    ) : (
                      <ChevronRight size={13} className="shrink-0 text-shell-text-tertiary" aria-hidden="true" />
                    )}
                  </button>
                  {isOpen && (
                    <div id={`sub-${sub.id}`} className="px-3 pb-3 flex flex-col gap-1.5 border-t border-white/5 pt-2">
                      {sub.description && (
                        <p className="text-xs text-shell-text-secondary leading-relaxed">{sub.description}</p>
                      )}
                      {sub.category && (
                        <span className="self-start px-2 py-0.5 rounded-full bg-accent/15 text-accent text-[10px] border border-accent/20">
                          {sub.category}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Raw lines toggle */}
      {Array.isArray(session.raw_lines) && session.raw_lines.length > 0 && (
        <div className="flex flex-col gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowRaw((v) => !v)}
            aria-expanded={showRaw}
            aria-controls="raw-lines-block"
            className="self-start h-7 px-2.5 gap-1.5 text-xs"
          >
            <FileText size={12} aria-hidden="true" />
            {showRaw ? 'Hide raw' : 'View raw'}
          </Button>
          {showRaw && (
            <pre
              id="raw-lines-block"
              className="text-[11px] text-shell-text-tertiary bg-white/[0.03] border border-white/8 rounded-md p-3 overflow-auto max-h-64 whitespace-pre-wrap leading-relaxed font-mono"
              aria-label="Raw archive lines"
            >
              {session.raw_lines.join('\n')}
            </pre>
          )}
        </div>
      )}
    </article>
  );
}
