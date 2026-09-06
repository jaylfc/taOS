import * as React from "react";
import { cn } from "@/lib/utils";

/**
 * Toolbar — a full-width horizontal bar rendering left/right groups
 * separated by a ToolbarSpacer. Follows the macOS Finder pattern: the
 * background always fills the available area regardless of how much
 * content each group has. Does not wrap — callers that need wrapping
 * should use a different component.
 *
 * Use Toolbar.Group to group related controls and ToolbarSpacer to push
 * groups to opposite edges.
 */
export const Toolbar = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        "flex flex-nowrap items-center gap-2 px-3 py-2 w-full bg-white/[0.03] border-b border-white/[0.06]",
        className,
      )}
      {...props}
    />
  ),
);
Toolbar.displayName = "Toolbar";

export const ToolbarGroup = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn("flex items-center gap-1", className)}
      {...props}
    />
  ),
);
ToolbarGroup.displayName = "ToolbarGroup";

export const ToolbarSpacer = () => <div className="flex-1 min-w-0" />;

export const ToolbarSeparator = () => <div className="w-px h-5 bg-white/10 mx-1" />;
