import { HTMLContainer, ShapeUtil, TLBaseShape, Rectangle2d, T } from "@tldraw/tldraw";

export type TaosLinkShape = TLBaseShape<
  "taos-link",
  {
    w: number;
    h: number;
    taos_kind: "link";
    taos_payload: {
      url: string; title: string; description: string;
      preview_image_url: string; favicon_url: string; fetched_at: number;
    };
    taos_author_id: string;
    taos_author_kind: "user" | "agent";
  }
>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class TaosLinkShapeUtil extends ShapeUtil<any> {
  static override type = "taos-link" as const;
  static override props = {
    w: T.number, h: T.number,
    taos_kind: T.literal("link"),
    taos_payload: T.object({
      url: T.string, title: T.string, description: T.string,
      preview_image_url: T.string, favicon_url: T.string, fetched_at: T.number,
    }),
    taos_author_id: T.string,
    taos_author_kind: T.literalEnum("user", "agent"),
  };

  override getDefaultProps(): TaosLinkShape["props"] {
    return {
      w: 320, h: 120,
      taos_kind: "link",
      taos_payload: {
        url: "", title: "", description: "",
        preview_image_url: "", favicon_url: "", fetched_at: 0,
      },
      taos_author_id: "user",
      taos_author_kind: "user",
    };
  }
  override getGeometry(shape: TaosLinkShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }
  override component(shape: TaosLinkShape) {
    const p = shape.props.taos_payload;
    return (
      <HTMLContainer
        style={{
          width: shape.props.w, height: shape.props.h,
          background: "white", border: "1px solid #d0d0d0",
          borderRadius: 6, overflow: "hidden", display: "flex",
        }}
      >
        {p.preview_image_url && (
          <img
            src={p.preview_image_url}
            alt=""
            style={{ width: 120, height: "100%", objectFit: "cover" }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
          />
        )}
        <div style={{ padding: 12, flex: 1, overflow: "hidden" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: 11, color: "#666", marginBottom: 4,
          }}>
            {p.favicon_url && <img src={p.favicon_url} alt="" style={{ width: 14, height: 14 }} />}
            <span>{(() => { try { return new URL(p.url).hostname; } catch { return p.url; } })()}</span>
          </div>
          <div style={{ fontWeight: 600, marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {p.title || p.url}
          </div>
          <div style={{ fontSize: 12, color: "#444", overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
            {p.description}
          </div>
        </div>
      </HTMLContainer>
    );
  }
  override indicator(shape: TaosLinkShape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={6} />;
  }
  override canResize() { return false; }
}
