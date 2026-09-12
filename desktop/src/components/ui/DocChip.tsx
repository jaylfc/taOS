import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const chipVariants = cva(
  "inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium text-shell-text-secondary transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40",
  {
    variants: {
      variant: {
        default: "bg-shell-surface border Shell-border-strong text-shell-text",
        accent: "bg-accent/10 text-accent border-accent-line",
        muted: "bg-shell-surface/50 text-shell-text-tertiary border-shell-border-weak",
      },
      size: {
        default: "h-6",
        sm: "h-5",
        lg: "h-7",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface DocChipProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof chipVariants> {
  name: string;
  fileSize: number;
  inFiles: boolean;
  onFetch?: () => void;
  asChild?: boolean;
}

const DocChip = React.forwardRef<HTMLDivElement, DocChipProps>(
  ({ className, variant, fileSize, inFiles, onFetch, size = "default", className: cc, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "div";

    return (
      <Comp
        ref={ref}
        className={cn(chipVariants({ variant, size, className: cc }), "size-full")}
        {...props}
      >
        <span className="truncate flex-1">{props.name ?? ""}</span>
        <span className="text-[10px] opacity-70">
          {fileSize === 0 ? "0 B" : fileSize > 1024 ? `${(fileSize / 1024).toFixed(1)} KB` : `${fileSize} B`}
        </span>
        {inFiles ? (
          <span className="ml-2 text-[10px] opacity-60">in Files</span>
        ) : (
          <span className="ml-2 text-[10px] opacity-60">shared</span>
        )}
        {onFetch && (
          <button
            onClick={onFetch}
            className="ml-2 rounded-md p-0.5 hover:bg-shell-surface-active transition-colors"
            aria-label="Fetch file"
            type="button"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-3 w-3"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path d="M12 2a2 2 0 0 1 2 2v4h4a2 2 0 0 1 0 4h-4v4a2 2 0 0 1-2 2h-4v-4A2 2 0 0 1 4 12H4a2 2 0 0 1-2-2v-4h-4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h4V2Zm0-2V1a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v3h3a1 1 0 0 1 1 1v9a1 1 0 0 1-1 1h-3v3a1 1 0 0 1-1 1h-5v-5H5v5a1 1 0 0 1-1 1V3a1 1 0 0 1 1-1h2v-1Z" />
            </svg>
          </button>
        )}
      </Comp>
    );
  },
);
DocChip.displayName = "DocChip";

export { DocChip };