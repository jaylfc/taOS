import { HTMLContainer, ShapeUtil, TLBaseShape, Rectangle2d, T } from "@tldraw/tldraw";

export type TaosImageShape = TLBaseShape<
  "taos-image",
  {
    w: number;
    h: number;
    taos_kind: "image";
    taos_payload: { file_id: string; alt: string; mime: string };
    taos_author_id: string;
    taos_author_kind: "user" | "agent";
    project_slug: string;
  }
>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class TaosImageShapeUtil extends ShapeUtil<any> {
  static override type = "taos-image" as const;
  static override props = {
    w: T.number, h: T.number,
    taos_kind: T.literal("image"),
    taos_payload: T.object({ file_id: T.string, alt: T.string, mime: T.string }),
    taos_author_id: T.string,
    taos_author_kind: T.literalEnum("user", "agent"),
    project_slug: T.string,
  };

  override getDefaultProps(): TaosImageShape["props"] {
    return {
      w: 240, h: 240, taos_kind: "image",
      taos_payload: { file_id: "", alt: "", mime: "image/png" },
      taos_author_id: "user", taos_author_kind: "user",
      project_slug: "",
    };
  }
  override getGeometry(shape: TaosImageShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }
  override component(shape: TaosImageShape) {
    const p = shape.props.taos_payload;
    const slug = shape.props.project_slug;
    const src = p.file_id ? `/api/projects/${slug}/files/canvas/${p.file_id}` : "";
    return (
      <HTMLContainer style={{ width: shape.props.w, height: shape.props.h }}>
        {src ? (
          <img src={src} alt={p.alt} style={{ width: "100%", height: "100%", objectFit: "contain" }} />
        ) : (
          <div style={{
            width: "100%", height: "100%", background: "#eee",
            display: "flex", alignItems: "center", justifyContent: "center",
            color: "#888", fontSize: 12,
          }}>
            (no image)
          </div>
        )}
      </HTMLContainer>
    );
  }
  override indicator(shape: TaosImageShape) {
    return <rect width={shape.props.w} height={shape.props.h} />;
  }
  override canResize() { return false; }
}
