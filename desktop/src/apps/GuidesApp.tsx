import { useState, useEffect, useCallback } from "react";
import { BookOpen, Cpu, ChevronDown, Sparkles } from "lucide-react";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Label,
} from "@/components/ui";

/* ------------------------------------------------------------------ */
/*  Types                                                              */
/* ------------------------------------------------------------------ */

interface Tier {
  label: string;
  description: string;
  icon: string;
}

interface UseCase {
  label: string;
  description: string;
  icon: string;
}

interface Recommendation {
  model: string;
  reason: string;
  note?: string;
}

interface RecommendationsResponse {
  hardware: string;
  use_case: string;
  recommendations: Recommendation[];
}

/* ------------------------------------------------------------------ */
/*  Guideline banner                                                   */
/* ------------------------------------------------------------------ */

function GuidelineBanner() {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-card/50 px-4 py-3 text-sm text-muted-foreground">
      <BookOpen size={16} className="shrink-0 text-accent" />
      <span>
        These are opinionated, curated recommendations — not mechanical
        compatibility checks. The Store tells you what <em>can</em> run; Guides
        tell you what <em>should</em> run.
      </span>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Recommendation card                                                */
/* ------------------------------------------------------------------ */

function RecCard({ rec }: { rec: Recommendation }) {
  return (
    <Card className="border-border bg-card/40 transition-colors hover:bg-card/60">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Sparkles size={16} className="shrink-0 text-amber-400" />
          {rec.model}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <p className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">Why: </span>
          {rec.reason}
        </p>
        {rec.note && (
          <p className="text-xs text-muted-foreground/70 italic">
            {rec.note}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ */
/*  Select dropdown                                                    */
/* ------------------------------------------------------------------ */

function StyledSelect({
  value,
  onChange,
  options,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { key: string; label: string; description: string }[];
  placeholder: string;
}) {
  return (
    <div className="relative w-full max-w-xs">
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full appearance-none rounded-lg border border-border bg-card px-3 py-2.5 pr-8 text-sm text-foreground focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
      >
        <option value="" disabled>
          {placeholder}
        </option>
        {options.map((opt) => (
          <option key={opt.key} value={opt.key}>
            {opt.label} — {opt.description}
          </option>
        ))}
      </select>
      <ChevronDown
        size={14}
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Main component                                                     */
/* ------------------------------------------------------------------ */

export function GuidesApp({ windowId: _windowId }: { windowId: string }) {
  const [tiers, setTiers] = useState<Record<string, Tier>>({});
  const [useCases, setUseCases] = useState<Record<string, UseCase>>({});
  const [selectedTier, setSelectedTier] = useState("");
  const [selectedCase, setSelectedCase] = useState("");
  const [recommendations, setRecommendations] = useState<Recommendation[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load tiers and use cases on mount
  useEffect(() => {
    async function loadMeta() {
      try {
        const [tiersRes, casesRes] = await Promise.all([
          fetch("/api/guides/tiers"),
          fetch("/api/guides/use-cases"),
        ]);
        if (tiersRes.ok) {
          const data = await tiersRes.json();
          setTiers(data.tiers || {});
        }
        if (casesRes.ok) {
          const data = await casesRes.json();
          setUseCases(data.use_cases || {});
        }
      } catch {
        // Fall back to empty meta — selectors show nothing
      }
    }
    loadMeta();
  }, []);

  // Fetch recommendations when both selectors have values
  const fetchRecs = useCallback(async () => {
    if (!selectedTier || !selectedCase) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/guides/recommendations?hardware=${encodeURIComponent(selectedTier)}&use_case=${encodeURIComponent(selectedCase)}`
      );
      if (!res.ok) {
        const body = await res.json();
        setError(body.detail || "Failed to load recommendations");
        setRecommendations(null);
        return;
      }
      const data: RecommendationsResponse = await res.json();
      setRecommendations(data.recommendations);
    } catch (e) {
      setError("Could not reach the server");
      setRecommendations(null);
    } finally {
      setLoading(false);
    }
  }, [selectedTier, selectedCase]);

  useEffect(() => {
    fetchRecs();
  }, [fetchRecs]);

  // Build option lists
  const tierOptions = Object.entries(tiers).map(([key, t]) => ({
    key,
    label: t.label,
    description: t.description,
  }));
  const caseOptions = Object.entries(useCases).map(([key, c]) => ({
    key,
    label: c.label,
    description: c.description,
  }));

  return (
    <div className="flex h-full flex-col gap-4 overflow-y-auto p-6">
      {/* Header */}
      <div className="flex items-center gap-2">
        <BookOpen size={22} className="text-accent" />
        <h1 className="text-xl font-semibold text-foreground">Model Guides</h1>
      </div>

      {/* Guideline banner */}
      <GuidelineBanner />

      {/* Selectors */}
      <div className="flex flex-wrap items-end gap-4">
        <div className="flex flex-col gap-1.5">
          <Label className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <Cpu size={12} /> Hardware
          </Label>
          <StyledSelect
            value={selectedTier}
            onChange={setSelectedTier}
            options={tierOptions}
            placeholder="Select hardware…"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <Label className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <Sparkles size={12} /> Use Case
          </Label>
          <StyledSelect
            value={selectedCase}
            onChange={setSelectedCase}
            options={caseOptions}
            placeholder="Select use case…"
          />
        </div>

        <Button
          variant="outline"
          size="sm"
          onClick={fetchRecs}
          disabled={!selectedTier || !selectedCase || loading}
        >
          {loading ? "Loading…" : "Get Recommendations"}
        </Button>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-400">
          {error}
        </div>
      )}

      {/* Results */}
      {recommendations && recommendations.length > 0 && (
        <div className="mt-2">
          <h2 className="mb-3 text-sm font-medium text-muted-foreground">
            Recommended for {tiers[selectedTier]?.label ?? selectedTier} —{" "}
            {useCases[selectedCase]?.label ?? selectedCase}
          </h2>
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
            {recommendations.map((rec, i) => (
              <RecCard key={i} rec={rec} />
            ))}
          </div>
        </div>
      )}

      {/* Empty state */}
      {recommendations && recommendations.length === 0 && !error && (
        <p className="text-sm text-muted-foreground">
          No recommendations yet for this combination. Check back soon!
        </p>
      )}

      {/* Initial prompt */}
      {!recommendations && !loading && !error && (
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <BookOpen size={48} className="text-muted-foreground/30" />
          <p className="text-sm text-muted-foreground">
            Select your hardware tier and use case above to see curated model
            recommendations.
          </p>
        </div>
      )}
    </div>
  );
}
