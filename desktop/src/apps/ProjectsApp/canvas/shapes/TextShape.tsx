import {
  HTMLContainer,
  ShapeUtil,
  TLBaseShape,
  Rectangle2d,
  T,
} from "@tldraw/tldraw";

export type TaosTextShape = TLBaseShape<
  "taos-text",
  {
    w: number;
    h: number;
    taos_kind: "text";
    taos_payload: { text: string };
    taos_author_id: string;
    taos_author_kind: "user" | "agent";
  }
>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class TaosTextShapeUtil extends ShapeUtil<any> {
  static override type = "taos-text" as const;
  static override props = {
    w: T.number, h: T.number,
    taos_kind: T.literal("text"),
    taos_payload: T.object({ text: T.string }),
    taos_author_id: T.string,
    taos_author_kind: T.literalEnum("user", "agent"),
  };

  override getDefaultProps(): TaosTextShape["props"] {
    return {
      w: 200, h: 100,
      taos_kind: "text",
      taos_payload: { text: "" },
      taos_author_id: "user",
      taos_author_kind: "user",
    };
  }
  override getGeometry(shape: TaosTextShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }
  override component(shape: TaosTextShape) {
    const { text } = shape.props.taos_payload;
    return (
      <HTMLContainer
        style={{
          width: shape.props.w, height: shape.props.h,
          background: "#ffffff", border: "1px solid rgba(0,0,0,0.15)",
          padding: 8, borderRadius: 4, fontSize: 14,
          whiteSpace: "pre-wrap", overflow: "hidden",
        }}
      >
        {shape.props.taos_author_kind === "agent" && (
          <div style={{ fontSize: 10, opacity: 0.5, marginBottom: 4 }}>
            by @{shape.props.taos_author_id}
          </div>
        )}
        {text}
      </HTMLContainer>
    );
  }
  override indicator(shape: TaosTextShape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={4} />;
  }
  override canResize() { return false; }
}
