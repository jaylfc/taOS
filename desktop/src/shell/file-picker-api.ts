import React from "react";
import { createRoot } from "react-dom/client";
import { FilePicker, type FileSelection } from "./FilePicker";

type Source = "disk" | "workspace" | "agent-workspace";

export function openFilePicker(opts: {
  sources: Source[];
  accept?: string;
  multi?: boolean;
}): Promise<FileSelection[]> {
  return new Promise((resolve) => {
    const container = document.createElement("div");
    document.body.appendChild(container);
    const root = createRoot(container);

    const cleanup = () => {
      root.unmount();
      container.remove();
    };

    root.render(
      React.createElement(FilePicker, {
        sources: opts.sources,
        accept: opts.accept,
        multi: opts.multi,
        onPick: (sels: FileSelection[]) => { cleanup(); resolve(sels); },
        onCancel: () => { cleanup(); resolve([]); },
      }),
    );
  });
}
