interface Props {
  onClick: () => void;
}

export function ProjectFab({ onClick }: Props) {
  return (
    <button
      type="button"
      aria-label="Create task"
      onClick={onClick}
      className="fixed right-4 z-40 flex h-14 w-14 items-center justify-center
                 rounded-full bg-blue-600 text-2xl font-light text-white shadow-lg
                 active:scale-95 transition-transform"
      style={{ bottom: "calc(env(safe-area-inset-bottom, 0px) + 1rem)" }}
    >
      +
    </button>
  );
}
