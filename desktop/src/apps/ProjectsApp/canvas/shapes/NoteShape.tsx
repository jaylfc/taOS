import {
  HTMLContainer,
  ShapeUtil,
  TLBaseShape,
  Rectangle2d,
  T,
} from "@tldraw/tldraw";

export type TaosNoteShape = TLBaseShape<
  "taos-note",
  {
    w: number;
    h: number;
    taos_kind: "note";
    taos_payload: { text: string; color: string; font_size: number };
    taos_author_id: string;
    taos_author_kind: "user" | "agent";
  }
>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class TaosNoteShapeUtil extends ShapeUtil<any> {
  static override type = "taos-note" as const;
  static override props = {
    w: T.number, h: T.number,
    taos_kind: T.literal("note"),
    taos_payload: T.object({
      text: T.string, color: T.string, font_size: T.number,
    }),
    taos_author_id: T.string,
    taos_author_kind: T.literalEnum("user", "agent"),
  };

  override getDefaultProps(): TaosNoteShape["props"] {
    return {
      w: 200, h: 100,
      taos_kind: "note",
      taos_payload: { text: "", color: "yellow", font_size: 14 },
      taos_author_id: "user",
      taos_author_kind: "user",
    };
  }
  override getGeometry(shape: TaosNoteShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }
  override component(shape: TaosNoteShape) {
    const { text, color, font_size } = shape.props.taos_payload;
    const bg = COLOR_MAP[color] ?? COLOR_MAP.yellow;
    return (
      <HTMLContainer
        style={{
          width: shape.props.w, height: shape.props.h,
          background: bg, border: "1px solid rgba(0,0,0,0.15)",
          padding: 8, borderRadius: 4, fontSize: font_size,
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
  override indicator(shape: TaosNoteShape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={4} />;
  }
  override canResize() { return false; }
}

const COLOR_MAP: Record<string, string> = {
  yellow: "#FFF3A1", blue: "#BEDDFF", green: "#C8F0BE",
  pink: "#FFC8E0", grey: "#E0E0E0",
};
