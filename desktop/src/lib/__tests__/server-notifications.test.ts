import { describe, it, expect } from "vitest";
import {
  mapRow,
  sourceToTarget,
  targetToAction,
  type ServerNotificationRow,
} from "../server-notifications";

function row(overrides: Partial<ServerNotificationRow> = {}): ServerNotificationRow {
  return {
    id: 7,
    timestamp: 1_700_000_000,
    level: "info",
    title: "Review requested",
    message: "plan.md is ready for review",
    read: false,
    source: "doc_review",
    data: null,
    ...overrides,
  };
}

describe("targetToAction", () => {
  it("routes a project_file target to the projects app Files tab", () => {
    const out = targetToAction({
      kind: "project_file",
      project_id: "prj-1",
      path: "docs/plan.md",
    });
    expect(out.action).toBe("projects");
    expect(out.meta).toEqual({
      projectId: "prj-1",
      tab: "files",
      filePath: "docs/plan.md",
    });
  });

  it("returns an empty object for an unknown kind", () => {
    expect(targetToAction({ kind: "not_a_real_kind", project_id: "prj-1" })).toEqual({});
  });

  it("returns an empty object when the target is missing or malformed", () => {
    expect(targetToAction(undefined)).toEqual({});
    expect(targetToAction(null)).toEqual({});
    expect(targetToAction("project_file")).toEqual({});
    expect(targetToAction({})).toEqual({});
    expect(targetToAction({ kind: 42 })).toEqual({});
  });

  it("tolerates a project_file target with missing fields", () => {
    const out = targetToAction({ kind: "project_file" });
    expect(out.action).toBe("projects");
    expect(out.meta).toEqual({ projectId: "", tab: "files", filePath: "" });
  });
});

describe("mapRow target precedence", () => {
  it("prefers a typed data.target over the source switch", () => {
    // The source alone would route to the Decisions app, but the typed target
    // wins and points at the project file.
    const n = mapRow(
      row({
        source: "decisions",
        data: { target: { kind: "project_file", project_id: "prj-9", path: "docs/audit.md" } },
      }),
    );
    expect(n.action).toBe("projects");
    expect(n.meta).toEqual({ projectId: "prj-9", tab: "files", filePath: "docs/audit.md" });
  });

  it("falls back to sourceToTarget when there is no target", () => {
    const n = mapRow(row({ source: "disk_quota", data: null }));
    expect(n.action).toBe("settings");
    expect(n.meta).toEqual({ section: "storage" });
    // Consistent with the standalone source mapping.
    expect(sourceToTarget("disk_quota")).toEqual({ action: "settings", meta: { section: "storage" } });
  });

  it("falls back to sourceToTarget when the target kind is unknown", () => {
    const n = mapRow(
      row({ source: "decisions", data: { target: { kind: "canvas_element", id: "el-1" } } }),
    );
    expect(n.action).toBe("decisions");
  });

  it("leaves a row non-navigable when neither target nor source routes", () => {
    const n = mapRow(row({ source: "mystery", data: null }));
    expect(n.action).toBeUndefined();
    expect(n.meta).toBeUndefined();
  });
});

describe("mapRow base mapping", () => {
  it("prefixes the id, scales the timestamp, and passes data through", () => {
    const data = { target: { kind: "project_file", project_id: "prj-1", path: "a.md" } };
    const n = mapRow(row({ id: 12, timestamp: 1_700_000_000, data }));
    expect(n.id).toBe("srv-12");
    expect(n.timestamp).toBe(1_700_000_000 * 1000);
    expect(n.data).toEqual(data);
  });

  it("clamps an unknown level to info", () => {
    expect(mapRow(row({ level: "bogus" })).level).toBe("info");
    expect(mapRow(row({ level: "error" })).level).toBe("error");
  });
});
