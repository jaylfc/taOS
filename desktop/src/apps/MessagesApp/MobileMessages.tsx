import { MessageCircle, Plus } from "lucide-react";
import { Button } from "@/components/ui";

/* ------------------------------------------------------------------
   MobileMessagesToolbar

   The MessagesApp header strip. On desktop it shows a "Messages"
   label and a "+" button to create a channel; on mobile the label is
   hidden (the in-pane channel header carries it) and the whole strip
   disappears once a channel is selected (the SplitView detail pane
   then owns the screen).

   Extracted from MessagesApp/index.tsx so the mobile-aware rendering
   lives next to MobileSplitView rather than being scattered through
   the desktop orchestrator. Behaviour, copy and styling are
   identical to the previous in-place JSX.
   ------------------------------------------------------------------ */

export interface MobileMessagesToolbarProps {
  /** When true the toolbar hides the "Messages" wordmark and the strip
   *  vanishes once a channel is selected. */
  isMobile: boolean;
  /** A channel id means the user has tapped into a chat and the strip
   *  should disappear (mobile) or stay (desktop). */
  hasSelectedChannel: boolean;
  /** Window title for the standalone chat PWA. Overrides the wordmark. */
  title?: string;
  onNewChannel: () => void;
}

export function MobileMessagesToolbar({
  isMobile,
  hasSelectedChannel,
  title,
  onNewChannel,
}: MobileMessagesToolbarProps) {
  // Mirror MessagesApp's showToolbar rule: on mobile the strip disappears
  // as soon as a channel is selected; desktop keeps it always.
  const showToolbar = !isMobile || !hasSelectedChannel;

  if (!showToolbar) return null;

  return (
    <div className="relative flex items-center px-3 py-2.5 border-b border-shell-border shrink-0">
      {title ? (
        <>
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <span className="text-sm font-semibold text-shell-text">{title}</span>
          </div>
          <div className="ml-auto">
            <Button
              variant="ghost"
              size="icon"
              onClick={onNewChannel}
              className="h-7 w-7"
              aria-label="New channel"
            >
              <Plus size={15} />
            </Button>
          </div>
        </>
      ) : (
        <>
          <div className="flex items-center gap-2 text-sm font-medium text-shell-text">
            <MessageCircle size={15} />
            {!isMobile && "Messages"}
          </div>
          <Button
            variant="ghost"
            size="icon"
            onClick={onNewChannel}
            className="h-7 w-7 ml-auto"
            aria-label="New channel"
          >
            <Plus size={15} />
          </Button>
        </>
      )}
    </div>
  );
}