import { useState, useEffect, useCallback } from "react";
import { onAppEvent, APP_INSTALLED } from "@/lib/app-event-bus";

export interface InstalledService {
  app_id: string;
  display_name: string;
  icon: string | null;
  url: string;
  category: string;
  backend: string;
  status: "running" | "stopped" | "unknown";
}

/**
 * Fetches the list of installed services from /api/apps/installed.
 * Re-fetches automatically when an app.installed event fires on the
 * shared EventBus (e.g. after a successful StoreApp install).
 * Returns the list (empty while loading or on error).
 */
export function useInstalledServices(): InstalledService[] {
  const [services, setServices] = useState<InstalledService[]>([]);

  const fetchServices = useCallback(() => {
    let cancelled = false;
    fetch("/api/apps/installed")
      .then((r) => (r.ok ? r.json() : []))
      .then((data: InstalledService[]) => {
        if (!cancelled) setServices(data);
      })
      .catch(() => {
        // Silently ignore — services section just won't appear
      });
    return () => { cancelled = true; };
  }, []);

  // Initial fetch
  useEffect(() => {
    return fetchServices();
  }, [fetchServices]);

  // Re-fetch when an app installs successfully
  useEffect(() => {
    return onAppEvent(APP_INSTALLED, () => { fetchServices(); });
  }, [fetchServices]);

  return services;
}
