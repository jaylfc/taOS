import { HTMLContainer, ShapeUtil, TLBaseShape, Rectangle2d, T } from "@tldraw/tldraw";

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
    return (
      <HTMLContainer
        style={{
          width: shape.props.w, height: shape.props.h,
          background: "#f4f4f4", border: "1px solid #d0d0d0",
          borderRadius: 4,
        }}
      />
    );
  }
  override indicator(shape: TaosGenericShape) {
    return <rect width={shape.props.w} height={shape.props.h} rx={4} />;
  }
  override canResize() { return false; }
}
