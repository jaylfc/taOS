declare module "react-grid-layout" {
  import * as React from "react";

  export interface Layout {
    i: string;
    x: number;
    y: number;
    w: number;
    h: number;
    minW?: number;
    minH?: number;
    maxW?: number;
    maxH?: number;
    static?: boolean;
    isDraggable?: boolean;
    isResizable?: boolean;
  }

  export interface ReactGridLayoutProps {
    className?: string;
    style?: React.CSSProperties;
    width: number;
    cols?: number;
    rowHeight?: number;
    layout?: Layout[];
    margin?: [number, number];
    containerPadding?: [number, number];
    isDraggable?: boolean;
    isResizable?: boolean;
    compactType?: "vertical" | "horizontal" | null;
    preventCollision?: boolean;
    draggableHandle?: string;
    draggableCancel?: string;
    onLayoutChange?: (layout: Layout[]) => void;
    onDragStart?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    onDrag?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    onDragStop?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    onResizeStart?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    onResize?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    onResizeStop?: (layout: Layout[], oldItem: Layout, newItem: Layout, placeholder: Layout, e: MouseEvent, element: HTMLElement) => void;
    children?: React.ReactNode;
  }

  const ReactGridLayout: React.FC<ReactGridLayoutProps>;
  export default ReactGridLayout;
  export { ReactGridLayout };
}

declare module "react-grid-layout/css/styles.css" {
  const content: string;
  export default content;
}

declare module "react-resizable/css/styles.css" {
  const content: string;
  export default content;
}
