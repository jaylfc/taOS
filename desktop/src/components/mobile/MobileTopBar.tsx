import { Search, Bell, ArrowUpCircle } from "lucide-react";
import { createPortal } from "react-dom";
import { useNotificationStore } from "@/stores/notification-store";
import { useUpdateAvailable } from "@/hooks/use-update-available";
import { useProcessStore } from "@/stores/process-store";
import { StatusIndicators } from "../StatusIndicators";
import { useIsPwa } from "@/hooks/use-is-pwa";

export const IOS_EDGE_BACKDROP_COLOR = "#141415";
export const IOS_EDGE_BACKDROP_HEIGHT = "1px";

interface Props {
  onSearch: () => void;
}

export const MOBILE_TOP_BAR_ROW_HEIGHT = 44;
/** Full rendered height, for overlays that must start below the bar. */
export const MOBILE_TOP_BAR_HEIGHT = `calc(env(safe-area-inset-top, 0px) + ${MOBILE_TOP_BAR_ROW_HEIGHT}px)`;

export function MobileTopBar({ onSearch }: Props) {
   const unreadCount = useNotificationStore((s) => s.notifications.filter((n) => !n.read).length);
   const toggleCentre = useNotificationStore((s) => s.toggleCentre);
   const hasUpdate = useUpdateAvailable();
   const openWindow = useProcessStore((s) => s.openWindow);
   const isPwa = useIsPwa();

   const openSettingsUpdates = () => {
    openWindow("settings", { w: 760, h: 520 }, { section: "updates" });
  };

  return (
    <div
      className="shrink-0"
      style={{
        paddingTop: "env(safe-area-inset-top, 0px)",
      }}
     >
       {isPwa && typeof document !== "undefined" && createPortal(
         <div
           aria-hidden="true"
           data-testid="ios-edge-backdrop"
           style={{
             position: "fixed",
             top: 0,
             left: 0,
             right: 0,
             // A 1px strip only: it masks the iOS 27 blur edge at the very top
             // while the wallpaper shows through behind the bar.
             height: IOS_EDGE_BACKDROP_HEIGHT,
             backgroundColor: IOS_EDGE_BACKDROP_COLOR,
             zIndex: 0,
             pointerEvents: "none",
           }}
         />,
         document.body,
       )}
       <div
         // No wordmark on the left: on the handset the front camera hole sits
         // there. Equal side columns keep the indicators centred, and pr-5
         // keeps the right buttons clear of the rounded screen corner.
         className="grid grid-cols-[1fr_auto_1fr] items-center pl-2 pr-5"
         style={{ height: MOBILE_TOP_BAR_ROW_HEIGHT }}
       >
         <div />

         {/* Centre — status indicators */}
         <div className="flex items-center justify-center">
           <StatusIndicators compact />
         </div>

         {/* Right — glass buttons */}
         <div className="flex items-center justify-end gap-2">
           <button
             onClick={onSearch}
             className="relative flex items-center justify-center active:opacity-60 transition-opacity"
             style={{
               width: 32,
               height: 32,
               borderRadius: "50%",
               background: "rgba(255,255,255,0.08)",
               backdropFilter: "blur(10px)",
               WebkitBackdropFilter: "blur(10px)",
               border: "1px solid rgba(255,255,255,0.1)",
             }}
             aria-label="Search"
           >
             <Search size={15} className="text-white/70" />
           </button>
           {hasUpdate && (
             <button
               onClick={openSettingsUpdates}
               className="relative flex items-center justify-center active:opacity-60 transition-opacity"
               style={{
                 width: 32,
                 height: 32,
                 borderRadius: "50%",
                 background: "rgba(255,255,255,0.08)",
                 backdropFilter: "blur(10px)",
                 WebkitBackdropFilter: "blur(10px)",
                 border: "1px solid rgba(255,255,255,0.1)",
               }}
               aria-label="Update available"
             >
               <ArrowUpCircle size={15} className="text-accent" />
               <span
                 className="absolute bg-accent rounded-full"
                 style={{ width: 5, height: 5, top: 6, right: 6 }}
               />
             </button>
           )}
           <button
             onClick={toggleCentre}
             className="relative flex items-center justify-center active:opacity-60 transition-opacity"
             style={{
               width: 32,
               height: 32,
               borderRadius: "50%",
               background: "rgba(255,255,255,0.08)",
               backdropFilter: "blur(10px)",
               WebkitBackdropFilter: "blur(10px)",
               border: "1px solid rgba(255,255,255,0.1)",
             }}
             aria-label={`Notifications${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
           >
             <Bell size={15} className="text-white/70" />
             {unreadCount > 0 && (
               <span
                 className="absolute bg-red-500 rounded-full"
                 style={{ width: 6, height: 6, top: 5, right: 5 }}
               />
             )}
           </button>
         </div>
       </div>
    </div>
  );
}
