import { HTMLContainer, ShapeUtil, TLBaseShape, Rectangle2d, T } from "@tldraw/tldraw";
import { genericPlaceholder } from "../element-to-shape";

// Fallback shape for any canvas kind that is not note/link/image.
// Custom taos_* data lives in shape.meta (see CanvasBoard.elementToShape),
// so this shape only declares geometry props. This keeps tldraw's built-in
// geo shape out of the round-trip, which would otherwise reject foreign props.
export type TaosGenericShape = TLBaseShape<
  "taos-generic",
  {
    w: number;
    h: number;
  }
>;

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export class TaosGenericShapeUtil extends ShapeUtil<any> {
  static override type = "taos-generic" as const;
  static override props = {
    w: T.number,
    h: T.number,
  };

  override getDefaultProps(): TaosGenericShape["props"] {
    return { w: 120, h: 120 };
  }
  override getGeometry(shape: TaosGenericShape) {
    return new Rectangle2d({ width: shape.props.w, height: shape.props.h, isFilled: true });
  }
  override component(shape: TaosGenericShape) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const meta = (shape.meta ?? {}) as any;
    const placeholder = genericPlaceholder(meta.taos_kind, meta.taos_payload);
    return (
      <HTMLContainer
        style={{
          width: shape.props.w, height: shape.props.h,
          background: "#f4f4f4", border: "1px solid #d0d0d0",
          borderRadius: 4,
          position: "relative", overflow: "hidden",
          display: "flex", alignItems: "center", justifyContent: "center",
          boxSizing: "border-box", padding: "18px 8px 8px",
        }}
      >
        {placeholder && (
          <>
            <span
              data-testid="generic-shape-badge"
              style={{
                position: "absolute", top: 4, left: 4,
                fontSize: 10, fontWeight: 600, lineHeight: "14px",
                padding: "0 5px", borderRadius: 3,
                background: "#e2e5ec", color: "#4a5163",
                textTransform: "uppercase", letterSpacing: "0.04em",
              }}
            >
              {placeholder.badge}
            </span>
            <span
              data-testid="generic-shape-label"
              style={{
                fontSize: 13, color: "#2c3142", textAlign: "center",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                maxWidth: "100%",
              }}
            >
              {placeholder.label}
            </span>
          </>
        )}
      </HTMLContainer>
    );
  }
  override indicator(shape: TaosGenericShape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={4} />;
  }
  override canResize() { return false; }
}
