import { ChevronLeft, Search, LayoutGrid, Layers } from "lucide-react";

interface Props {
  onBack: () => void;
  onHome: () => void;
  onSearch: () => void;
  onSwitcher: () => void;
  hasActiveApp: boolean;
}

export function MobileBottomNav({ onBack, onHome, onSearch, onSwitcher, hasActiveApp }: Props) {
  return (
    <nav
      className="shrink-0 flex items-center justify-around"
      style={{
        height: 48,
        backgroundColor: "var(--color-dock-bg)",
        borderTop: "1px solid rgba(255, 255, 255, 0.1)",
        backdropFilter: "blur(20px)",
        WebkitBackdropFilter: "blur(20px)",
        zIndex: 9500,
      }}
      aria-label="Navigation"
    >
      <button
        onClick={onBack}
        className={`flex items-center justify-center w-14 h-10 rounded-lg active:bg-white/10 transition-colors ${hasActiveApp ? "text-white/70" : "text-white/30"}`}
        aria-label="Back"
        disabled={!hasActiveApp}
      >
        <ChevronLeft size={22} />
      </button>

      <button
        onClick={onHome}
        className="flex items-center justify-center w-14 h-10 rounded-lg active:bg-white/10 transition-colors text-white/70"
        aria-label="Home"
      >
        <LayoutGrid size={20} />
      </button>

      <button
        onClick={onSearch}
        className="flex items-center justify-center w-14 h-10 rounded-lg active:bg-white/10 transition-colors text-white/70"
        aria-label="Search"
      >
        <Search size={20} />
      </button>

      <button
        onClick={onSwitcher}
        className="flex items-center justify-center w-14 h-10 rounded-lg active:bg-white/10 transition-colors text-white/70"
        aria-label="App Switcher"
      >
        <Layers size={20} />
      </button>
    </nav>
  );
}
