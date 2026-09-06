import { useEffect, useRef, useState } from "react";
import { projectsApi, type ProjectEvent } from "../../../lib/projects";

export type BoardLiveEvent = ProjectEvent;

export function useBoardLive(projectId: string, onEvent: (e: BoardLiveEvent) => void) {
  const [connected, setConnected] = useState(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  useEffect(() => {
    let active = true;
    const off = projectsApi.subscribeEvents(projectId, (e) => {
      if (active) onEventRef.current(e);
    });
    setConnected(true);
    return () => {
      active = false;
      setConnected(false);
      off();
    };
  }, [projectId]);

  return { connected };
}
