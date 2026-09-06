import { Text, Image as KonvaImage, Rect, Ellipse, Line } from "react-konva";
import type Konva from "konva";
import type { CanvasElement } from "./types";
import { useHtmlImage } from "./useHtmlImage";

export interface CanvasNodeProps {
  element: CanvasElement;
  isSelected: boolean;
  onSelect: (id: string) => void;
  onDragEnd: (id: string, patch: Partial<CanvasElement>) => void;
  onTransformEnd: (id: string, patch: Partial<CanvasElement>) => void;
  onDblClick: (id: string) => void;
  registerNode: (id: string, node: Konva.Node | null) => void;
}

/** Renders one canvas element as the matching Konva primitive, wired for
 * select / drag / resize+rotate (via the shared Transformer in DesignView). */
export function CanvasNode({
  element: el,
  isSelected,
  onSelect,
  onDragEnd,
  onTransformEnd,
  onDblClick,
  registerNode,
}: CanvasNodeProps) {
  const { image, failed: imageFailed } = useHtmlImage(el.type === "image" ? el.src : undefined);

  if (!el.visible) return null;

  const common = {
    id: el.id,
    draggable: true,
    onClick: () => onSelect(el.id),
    onTap: () => onSelect(el.id),
    onDblClick: () => onDblClick(el.id),
    onDblTap: () => onDblClick(el.id),
    ref: (node: Konva.Node | null) => registerNode(el.id, node),
    shadowColor: "#8bb4ff",
    shadowBlur: isSelected ? 14 : 0,
    shadowOpacity: isSelected ? 0.85 : 0,
    shadowEnabled: isSelected,
  };

  const handleRectLikeDragEnd = (e: { target: Konva.Node }) => {
    onDragEnd(el.id, { x: e.target.x(), y: e.target.y() });
  };

  const handleRectLikeTransformEnd = (e: { target: Konva.Node }) => {
    const node = e.target;
    const scaleX = node.scaleX();
    const scaleY = node.scaleY();
    node.scaleX(1);
    node.scaleY(1);
    onTransformEnd(el.id, {
      x: node.x(),
      y: node.y(),
      rotation: node.rotation(),
      width: Math.max(5, el.width * scaleX),
      height: Math.max(5, el.height * scaleY),
    });
  };

  switch (el.type) {
    case "text":
      return (
        <Text
          {...common}
          x={el.x}
          y={el.y}
          width={el.width}
          height={el.height}
          rotation={el.rotation}
          text={el.text}
          fontSize={el.fontSize}
          fontFamily={el.fontFamily}
          fill={el.fill}
          align={el.align}
          onDragEnd={handleRectLikeDragEnd}
          onTransformEnd={handleRectLikeTransformEnd}
        />
      );

    case "image":
      if (imageFailed) {
        // Broken-image placeholder: a dashed rect stands in for the missing
        // bitmap so a failed Magic/upload image is visible instead of blank.
        return (
          <Rect
            {...common}
            name="broken-image-placeholder"
            x={el.x}
            y={el.y}
            width={el.width}
            height={el.height}
            rotation={el.rotation}
            fill="#2a2e3a"
            stroke="#e0575c"
            strokeWidth={2}
            dash={[8, 6]}
            onDragEnd={handleRectLikeDragEnd}
            onTransformEnd={handleRectLikeTransformEnd}
          />
        );
      }
      return (
        <KonvaImage
          {...common}
          x={el.x}
          y={el.y}
          width={el.width}
          height={el.height}
          rotation={el.rotation}
          image={image}
          onDragEnd={handleRectLikeDragEnd}
          onTransformEnd={handleRectLikeTransformEnd}
        />
      );

    case "rect":
      return (
        <Rect
          {...common}
          x={el.x}
          y={el.y}
          width={el.width}
          height={el.height}
          rotation={el.rotation}
          fill={el.fill}
          stroke={el.stroke}
          strokeWidth={el.strokeWidth}
          onDragEnd={handleRectLikeDragEnd}
          onTransformEnd={handleRectLikeTransformEnd}
        />
      );

    case "ellipse": {
      const radiusX = el.width / 2;
      const radiusY = el.height / 2;
      return (
        <Ellipse
          {...common}
          x={el.x + radiusX}
          y={el.y + radiusY}
          radiusX={radiusX}
          radiusY={radiusY}
          rotation={el.rotation}
          fill={el.fill}
          stroke={el.stroke}
          strokeWidth={el.strokeWidth}
          onDragEnd={(e) => {
            const node = e.target;
            onDragEnd(el.id, { x: node.x() - radiusX, y: node.y() - radiusY });
          }}
          onTransformEnd={(e) => {
            const node = e.target;
            const scaleX = node.scaleX();
            const scaleY = node.scaleY();
            node.scaleX(1);
            node.scaleY(1);
            const newRadiusX = Math.max(2.5, radiusX * scaleX);
            const newRadiusY = Math.max(2.5, radiusY * scaleY);
            onTransformEnd(el.id, {
              x: node.x() - newRadiusX,
              y: node.y() - newRadiusY,
              rotation: node.rotation(),
              width: newRadiusX * 2,
              height: newRadiusY * 2,
            });
          }}
        />
      );
    }

    case "line":
      return (
        <Line
          {...common}
          x={el.x}
          y={el.y}
          points={[0, 0, el.width, 0]}
          rotation={el.rotation}
          stroke={el.stroke}
          strokeWidth={el.strokeWidth}
          hitStrokeWidth={Math.max(16, el.strokeWidth + 12)}
          onDragEnd={handleRectLikeDragEnd}
          onTransformEnd={(e) => {
            const node = e.target;
            const scaleX = node.scaleX();
            node.scaleX(1);
            node.scaleY(1);
            onTransformEnd(el.id, {
              x: node.x(),
              y: node.y(),
              rotation: node.rotation(),
              width: Math.max(10, el.width * scaleX),
            });
          }}
        />
      );

    default:
      return null;
  }
}
