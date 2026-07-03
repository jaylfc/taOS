import {
  PALETTES,
  FONTS,
  type Site,
  type Section,
  type HeroContent,
  type FeaturesContent,
  type TextBlockContent,
  type GalleryContent,
  type CtaContent,
  type ContactContent,
  type FooterContent,
} from "./types";

function esc(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function imageOrPlaceholder(src: string, alt: string, cls: string): string {
  if (src) return `<img class="${cls}" src="${esc(src)}" alt="${esc(alt)}" />`;
  return `<div class="${cls} ws-ph" role="img" aria-label="${esc(alt)}"></div>`;
}

function renderSection(section: Section): string {
  const c = section.content;
  switch (section.type) {
    case "hero": {
      const h = c as HeroContent;
      return `<section class="ws-hero">
  <div class="ws-hero-copy">
    <p class="ws-eyebrow">${esc(h.eyebrow)}</p>
    <h1>${esc(h.heading)}</h1>
    <p class="ws-sub">${esc(h.subheading)}</p>
    <a class="ws-btn" href="#">${esc(h.ctaLabel)}</a>
  </div>
  ${imageOrPlaceholder(h.image, "Hero image", "ws-hero-img")}
</section>`;
    }
    case "features": {
      const f = c as FeaturesContent;
      const items = f.items
        .map(
          (it) =>
            `<div class="ws-feature"><h3>${esc(it.title)}</h3><p>${esc(it.body)}</p></div>`,
        )
        .join("\n      ");
      return `<section class="ws-features">
  <h2>${esc(f.heading)}</h2>
  <div class="ws-grid-3">
      ${items}
  </div>
</section>`;
    }
    case "textBlock": {
      const t = c as TextBlockContent;
      return `<section class="ws-text">
  <h2>${esc(t.heading)}</h2>
  <p>${esc(t.body)}</p>
</section>`;
    }
    case "gallery": {
      const g = c as GalleryContent;
      const cells = g.images
        .map((src, i) => imageOrPlaceholder(src, `Image ${i + 1}`, "ws-gcell"))
        .join("\n      ");
      return `<section class="ws-gallery">
  <h2>${esc(g.heading)}</h2>
  <div class="ws-grid-img">
      ${cells}
  </div>
</section>`;
    }
    case "cta": {
      const t = c as CtaContent;
      return `<section class="ws-cta">
  <h2>${esc(t.heading)}</h2>
  <p>${esc(t.body)}</p>
  <a class="ws-btn" href="#">${esc(t.buttonLabel)}</a>
</section>`;
    }
    case "contact": {
      const t = c as ContactContent;
      return `<section class="ws-contact">
  <h2>${esc(t.heading)}</h2>
  <ul>
    <li><strong>Email</strong> ${esc(t.email)}</li>
    <li><strong>Phone</strong> ${esc(t.phone)}</li>
    <li><strong>Address</strong> ${esc(t.address)}</li>
  </ul>
</section>`;
    }
    case "footer": {
      const t = c as FooterContent;
      return `<footer class="ws-footer">
  <strong>${esc(t.businessName)}</strong>
  <span>${esc(t.tagline)}</span>
</footer>`;
    }
    default: {
      const exhaustive: never = section.type;
      throw new Error(`Unknown section type: ${String(exhaustive)}`);
    }
  }
}

/** Serialize a Site to a self-contained static HTML document string. */
export function exportSiteHtml(site: Site): string {
  const p = PALETTES[site.theme.palette].colors;
  const font = FONTS[site.theme.font].stack;
  const body = site.sections.map(renderSection).join("\n");

  const css = `:root{--bg:${p.bg};--surface:${p.surface};--text:${p.text};--muted:${p.muted};--accent:${p.accent};--accent-text:${p.accentText};--border:${p.border};}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:${font};background:var(--bg);color:var(--text);line-height:1.6}
section,footer{padding:64px 24px}
h1{font-size:clamp(30px,6vw,52px);line-height:1.1;letter-spacing:-0.02em}
h2{font-size:clamp(22px,4vw,32px);margin-bottom:24px;letter-spacing:-0.01em}
h3{font-size:18px;margin-bottom:8px}
p{color:var(--muted);max-width:60ch}
.ws-eyebrow{text-transform:uppercase;letter-spacing:0.14em;font-size:12px;color:var(--accent);margin-bottom:14px}
.ws-btn{display:inline-block;margin-top:24px;background:var(--accent);color:var(--accent-text);padding:12px 22px;border-radius:10px;text-decoration:none;font-weight:700}
.ws-hero{display:flex;gap:40px;align-items:center;flex-wrap:wrap;max-width:1040px;margin:0 auto}
.ws-hero-copy{flex:1;min-width:280px}
.ws-hero .ws-sub{font-size:18px;margin-top:16px}
.ws-hero-img{flex:1;min-width:280px;height:300px;border-radius:16px;object-fit:cover;width:100%}
.ws-features,.ws-text,.ws-gallery,.ws-cta,.ws-contact{max-width:1040px;margin:0 auto}
.ws-grid-3{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.ws-feature{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:22px}
.ws-grid-img{display:grid;grid-template-columns:repeat(3,1fr);gap:14px}
.ws-gcell{aspect-ratio:1/1;border-radius:12px;object-fit:cover;width:100%}
.ws-ph{background:var(--surface);border:1px dashed var(--border)}
.ws-cta{text-align:center;background:var(--surface);border-radius:20px;padding:56px 24px}
.ws-contact ul{list-style:none;display:grid;gap:10px}
.ws-contact li{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:12px 16px}
.ws-footer{display:flex;flex-direction:column;gap:4px;align-items:center;background:var(--surface);color:var(--muted);text-align:center}
.ws-footer strong{color:var(--text)}
@media(max-width:720px){.ws-grid-3{grid-template-columns:1fr}.ws-grid-img{grid-template-columns:repeat(2,1fr)}}`;

  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${esc(site.title)}</title>
<style>
${css}
</style>
</head>
<body>
${body}
</body>
</html>`;
}

/** Trigger a browser download of the site as a .html file. */
export function downloadSiteHtml(site: Site): void {
  const html = exportSiteHtml(site);
  const slug =
    site.title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "") || "site";
  const blob = new Blob([html], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slug}.html`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
