import { useRef } from "react";
import { ImagePlus } from "lucide-react";
import {
  type Section,
  type SectionContent,
  type PaletteColors,
  type HeroContent,
  type FeaturesContent,
  type TextBlockContent,
  type GalleryContent,
  type CtaContent,
  type ContactContent,
  type FooterContent,
} from "./types";

interface EditableProps {
  value: string;
  onCommit: (v: string) => void;
  ariaLabel: string;
  editable: boolean;
  className?: string;
  style?: React.CSSProperties;
  as?: "div" | "span" | "h1" | "h2" | "h3" | "p";
}

/** Inline contentEditable text that commits on blur. Uncontrolled while
 *  focused (no cursor jitter); keyed by value so external loads re-seed it. */
function EditableText({
  value,
  onCommit,
  ariaLabel,
  editable,
  className,
  style,
  as = "div",
}: EditableProps) {
  const Tag: React.ElementType = as;
  if (!editable) {
    return (
      <Tag className={className} style={style}>
        {value}
      </Tag>
    );
  }
  return (
    <Tag
      key={value}
      role="textbox"
      aria-label={ariaLabel}
      tabIndex={0}
      contentEditable
      suppressContentEditableWarning
      spellCheck={false}
      className={className}
      style={{ outline: "none", ...style }}
      onClick={(e: React.MouseEvent) => e.stopPropagation()}
      onBlur={(e: React.FocusEvent<HTMLElement>) => {
        const next = e.currentTarget.textContent ?? "";
        if (next !== value) onCommit(next);
      }}
    >
      {value}
    </Tag>
  );
}

/** Images are inlined as data URIs into the site's stored JSON, so an
 *  oversized upload would bloat every save; keep it well under the
 *  backend's MAX_CONTENT_BYTES cap on the whole site document. */
const MAX_IMAGE_BYTES = 2 * 1024 * 1024; // 2 MB

function readImage(file: File): Promise<string> {
  if (!file.type.startsWith("image/")) {
    return Promise.reject(new Error(`"${file.name}" is not an image file.`));
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return Promise.reject(
      new Error(`"${file.name}" is larger than ${MAX_IMAGE_BYTES / (1024 * 1024)} MB.`),
    );
  }
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function ImageSlot({
  src,
  alt,
  onPick,
  editable,
  className,
  sizeStyle,
  placeholderBg,
  placeholderBorder,
}: {
  src: string;
  alt: string;
  onPick: (dataUri: string) => void;
  editable: boolean;
  className: string;
  sizeStyle: React.CSSProperties;
  placeholderBg: string;
  placeholderBorder: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const style: React.CSSProperties = {
    ...sizeStyle,
    borderRadius: 14,
    ...(src
      ? { backgroundImage: `url(${src})`, backgroundSize: "cover", backgroundPosition: "center" }
      : { background: placeholderBg, border: `1px dashed ${placeholderBorder}` }),
  };
  return (
    <div className={className} style={style}>
      {editable && (
        <>
          <button
            type="button"
            aria-label={`Swap ${alt}`}
            onClick={(e) => {
              e.stopPropagation();
              inputRef.current?.click();
            }}
            className="flex h-full w-full items-center justify-center gap-1.5 text-[11px] font-semibold opacity-0 transition-opacity hover:opacity-100"
            style={{ background: "rgba(0,0,0,0.45)", color: "#fff" }}
          >
            <ImagePlus size={15} />
            {src ? "Replace" : "Add image"}
          </button>
          <input
            ref={inputRef}
            type="file"
            accept="image/*"
            className="hidden"
            aria-label={`Upload ${alt}`}
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (file) {
                try {
                  onPick(await readImage(file));
                } catch (err) {
                  window.alert(err instanceof Error ? err.message : "Could not read image");
                }
              }
              e.target.value = "";
            }}
          />
        </>
      )}
    </div>
  );
}

interface BlockProps {
  section: Section;
  colors: PaletteColors;
  fontStack: string;
  editable: boolean;
  selected?: boolean;
  onSelect?: () => void;
  onChange?: (content: SectionContent) => void;
}

export function SectionBlock({
  section,
  colors,
  fontStack,
  editable,
  selected,
  onSelect,
  onChange,
}: BlockProps) {
  const patch = (next: Partial<SectionContent>) =>
    onChange?.({ ...(section.content as object), ...next } as SectionContent);

  const wrap: React.CSSProperties = {
    fontFamily: fontStack,
    background: colors.bg,
    color: colors.text,
    padding: "48px 40px",
    position: "relative",
    cursor: editable ? "pointer" : "default",
    boxShadow: selected ? `inset 0 0 0 2px ${colors.accent}` : undefined,
  };
  const btn: React.CSSProperties = {
    display: "inline-block",
    marginTop: 18,
    background: colors.accent,
    color: colors.accentText,
    padding: "10px 18px",
    borderRadius: 10,
    fontWeight: 700,
    fontSize: 14,
  };
  const card: React.CSSProperties = {
    background: colors.surface,
    border: `1px solid ${colors.border}`,
    borderRadius: 14,
    padding: 18,
  };

  const inner = (() => {
    switch (section.type) {
      case "hero": {
        const h = section.content as HeroContent;
        return (
          <div style={{ display: "flex", gap: 32, flexWrap: "wrap", alignItems: "center" }}>
            <div style={{ flex: 1, minWidth: 240 }}>
              <EditableText editable={editable} value={h.eyebrow} onCommit={(v) => patch({ eyebrow: v })} ariaLabel="Hero eyebrow" style={{ textTransform: "uppercase", letterSpacing: "0.14em", fontSize: 12, color: colors.accent, marginBottom: 10 }} />
              <EditableText as="h1" editable={editable} value={h.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="Hero heading" style={{ fontSize: 38, lineHeight: 1.1, letterSpacing: "-0.02em", fontWeight: 800 }} />
              <EditableText as="p" editable={editable} value={h.subheading} onCommit={(v) => patch({ subheading: v })} ariaLabel="Hero subheading" style={{ color: colors.muted, marginTop: 14, fontSize: 17 }} />
              <span style={btn}>{h.ctaLabel}</span>
            </div>
            <ImageSlot editable={editable} src={h.image} alt="hero image" onPick={(img) => patch({ image: img })} className="relative overflow-hidden" sizeStyle={{ flex: 1, minWidth: 240, height: 260 }} placeholderBg={colors.surface} placeholderBorder={colors.border} />
          </div>
        );
      }
      case "features": {
        const f = section.content as FeaturesContent;
        return (
          <div>
            <EditableText as="h2" editable={editable} value={f.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="Features heading" style={{ fontSize: 26, marginBottom: 20, fontWeight: 700 }} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 16 }}>
              {f.items.map((it, i) => (
                <div key={i} style={card}>
                  <EditableText as="h3" editable={editable} value={it.title} onCommit={(v) => patch({ items: f.items.map((x, j) => (j === i ? { ...x, title: v } : x)) })} ariaLabel={`Feature ${i + 1} title`} style={{ fontWeight: 700, marginBottom: 6 }} />
                  <EditableText as="p" editable={editable} value={it.body} onCommit={(v) => patch({ items: f.items.map((x, j) => (j === i ? { ...x, body: v } : x)) })} ariaLabel={`Feature ${i + 1} body`} style={{ color: colors.muted, fontSize: 14 }} />
                </div>
              ))}
            </div>
          </div>
        );
      }
      case "textBlock": {
        const t = section.content as TextBlockContent;
        return (
          <div>
            <EditableText as="h2" editable={editable} value={t.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="Text block heading" style={{ fontSize: 26, marginBottom: 14, fontWeight: 700 }} />
            <EditableText as="p" editable={editable} value={t.body} onCommit={(v) => patch({ body: v })} ariaLabel="Text block body" style={{ color: colors.muted, maxWidth: "62ch", lineHeight: 1.7 }} />
          </div>
        );
      }
      case "gallery": {
        const g = section.content as GalleryContent;
        return (
          <div>
            <EditableText as="h2" editable={editable} value={g.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="Gallery heading" style={{ fontSize: 26, marginBottom: 16, fontWeight: 700 }} />
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12 }}>
              {g.images.map((src, i) => (
                <ImageSlot key={i} editable={editable} src={src} alt={`gallery image ${i + 1}`} onPick={(img) => patch({ images: g.images.map((x, j) => (j === i ? img : x)) })} className="relative overflow-hidden" sizeStyle={{ aspectRatio: "1 / 1" }} placeholderBg={colors.surface} placeholderBorder={colors.border} />
              ))}
            </div>
          </div>
        );
      }
      case "cta": {
        const t = section.content as CtaContent;
        return (
          <div style={{ textAlign: "center", background: colors.surface, borderRadius: 18, padding: "44px 24px" }}>
            <EditableText as="h2" editable={editable} value={t.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="CTA heading" style={{ fontSize: 26, marginBottom: 10, fontWeight: 700 }} />
            <EditableText as="p" editable={editable} value={t.body} onCommit={(v) => patch({ body: v })} ariaLabel="CTA body" style={{ color: colors.muted, marginLeft: "auto", marginRight: "auto" }} />
            <div><span style={btn}>{t.buttonLabel}</span></div>
          </div>
        );
      }
      case "contact": {
        const t = section.content as ContactContent;
        return (
          <div>
            <EditableText as="h2" editable={editable} value={t.heading} onCommit={(v) => patch({ heading: v })} ariaLabel="Contact heading" style={{ fontSize: 26, marginBottom: 16, fontWeight: 700 }} />
            <div style={{ display: "grid", gap: 10, maxWidth: 420 }}>
              <div style={card}><EditableText editable={editable} value={t.email} onCommit={(v) => patch({ email: v })} ariaLabel="Contact email" /></div>
              <div style={card}><EditableText editable={editable} value={t.phone} onCommit={(v) => patch({ phone: v })} ariaLabel="Contact phone" /></div>
              <div style={card}><EditableText editable={editable} value={t.address} onCommit={(v) => patch({ address: v })} ariaLabel="Contact address" /></div>
            </div>
          </div>
        );
      }
      case "footer": {
        const t = section.content as FooterContent;
        return (
          <div style={{ textAlign: "center", color: colors.muted }}>
            <EditableText editable={editable} value={t.businessName} onCommit={(v) => patch({ businessName: v })} ariaLabel="Footer business name" style={{ color: colors.text, fontWeight: 700 }} />
            <EditableText editable={editable} value={t.tagline} onCommit={(v) => patch({ tagline: v })} ariaLabel="Footer tagline" style={{ fontSize: 13 }} />
          </div>
        );
      }
      default: {
        // Corrupted or unrecognized section data (e.g. an old/foreign
        // export) should be visible, not silently render as an empty div.
        const exhaustive: never = section.type;
        return (
          <div style={{ padding: 24, textAlign: "center", color: colors.muted }}>
            Unsupported section type: {String(exhaustive)}
          </div>
        );
      }
    }
  })();

  return (
    <div
      style={wrap}
      onClick={editable ? onSelect : undefined}
      data-section-type={section.type}
      data-section-id={section.id}
    >
      {inner}
    </div>
  );
}
