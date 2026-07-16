export type Project = {
  id: string;
  name: string;
  slug: string;
  description: string;
  status: "active" | "archived" | "deleted";
  created_by: string;
  created_at: number;
  updated_at: number;
  lead_member_id?: string | null;
};

export type ProjectMember = {
  project_id: string;
  member_id: string;
  member_kind: "native" | "clone";
  role: string;
  source_agent_id: string | null;
  memory_seed: "none" | "snapshot" | "empty";
  added_at: number;
  can_edit_canvas?: boolean;
  can_read_canvas?: boolean;
  is_lead?: number;
};

export type ProjectTask = {
  id: string;
  project_id: string;
  parent_task_id: string | null;
  title: string;
  body: string;
  status: "open" | "claimed" | "closed" | "cancelled";
  priority: number;
  labels: string[];
  assignee_id: string | null;
  claimed_by: string | null;
  claimed_at: number | null;
  closed_at: number | null;
  closed_by: string | null;
  close_reason: string | null;
  element_id: string | null;
  created_by: string;
  created_at: number;
  updated_at: number;
};

export type ElementType =
  | "generic"
  | "code"
  | "website"
  | "design"
  | "docs"
  | "marketing"
  | "business"
  | (string & {});

export type ProjectElement = {
  id: string;
  project_id: string;
  name: string;
  slug: string;
  type: ElementType;
  description: string;
  assignee_id: string | null;
  settings: Record<string, unknown>;
  created_at: number;
  updated_at: number;
  archived_at: number | null;
  open_tasks?: number;
  total_tasks?: number;
  canvas_items?: number;
};

export type ProjectActivity = {
  id: number;
  project_id: string;
  actor_id: string;
  kind: string;
  payload: Record<string, unknown>;
  created_at: number;
};

export type ProjectComment = {
  id: string;
  task_id: string;
  author_id: string;
  body: string;
  replies_to_comment_id: string | null;
  created_at: number;
};

export type ProjectRelationship = {
  id: string;
  project_id: string;
  from_task_id: string;
  to_task_id: string;
  kind: string;
  created_by: string;
  created_at: number;
};

export type ProjectEvent = {
  kind: string;
  payload: Record<string, unknown>;
  ts: number;
};

export type Routine = {
  id: string;
  project_id: string;
  title: string;
  body_template: string;
  assignee_id: string | null;
  trigger_kind: "cron" | "webhook" | "api";
  cron_expr: string | null;
  webhook_token: string | null;
  enabled: number;
  last_fired: number | null;
  next_fire: number | null;
  created_by: string;
  created_at: number;
  updated_at: number;
};

export type TaskContextAncestor = { id: string; title: string; status: string };
export type TaskContextBlocker = { id: string; title: string; status: string };

export type TaskContext = {
  project: { id: string; name: string | null; description: string | null };
  ancestry: TaskContextAncestor[];
  blockers: TaskContextBlocker[];
  is_blocked: boolean;
};

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text().catch(() => r.statusText);
    throw new Error(`${r.status}: ${text}`);
  }
  return (await r.json()) as T;
}

export const projectsApi = {
  list: (status: string = "active") =>
    http<{ items: Project[] }>(`/api/projects?status=${status}`).then((r) => r.items),
  get: (id: string) => http<Project>(`/api/projects/${id}`),
  create: (input: { name: string; slug: string; description?: string }) =>
    http<Project>("/api/projects", { method: "POST", body: JSON.stringify(input) }),
  update: (id: string, patch: Partial<Pick<Project, "name" | "description">>) =>
    http<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  archive: (id: string) =>
    http<Project>(`/api/projects/${id}/archive`, { method: "POST" }),
  remove: (id: string) =>
    http<Project>(`/api/projects/${id}`, { method: "DELETE" }),
  // D7: the Lead is an exclusive, project-level designation. Pass a member id
  // to promote that member, or null to clear the lead.
  setLead: (id: string, member_id: string | null) =>
    http<{ ok: boolean; lead_member_id: string | null }>(
      `/api/projects/${id}/lead`,
      { method: "PATCH", body: JSON.stringify({ member_id }) },
    ),

  members: {
    list: (pid: string) =>
      http<{ items: ProjectMember[] }>(`/api/projects/${pid}/members`).then((r) => r.items),
    addNative: (pid: string, agent_id: string) =>
      http<ProjectMember>(`/api/projects/${pid}/members`, {
        method: "POST",
        body: JSON.stringify({ mode: "native", agent_id }),
      }),
    addClone: (pid: string, source_agent_id: string, clone_memory: boolean) =>
      http<ProjectMember>(`/api/projects/${pid}/members`, {
        method: "POST",
        body: JSON.stringify({ mode: "clone", source_agent_id, clone_memory }),
      }),
    remove: (pid: string, member_id: string) =>
      http<{ ok: boolean }>(`/api/projects/${pid}/members/${member_id}`, { method: "DELETE" }),
  },

  tasks: {
    list: (pid: string, status?: string) =>
      http<{ items: ProjectTask[] }>(
        `/api/projects/${pid}/tasks${status ? `?status=${status}` : ""}`,
      ).then((r) => r.items),
    ready: (pid: string) =>
      http<{ items: ProjectTask[] }>(`/api/projects/${pid}/tasks/ready`).then((r) => r.items),
    create: (pid: string, input: { title: string; body?: string; priority?: number }) =>
      http<ProjectTask>(`/api/projects/${pid}/tasks`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    claim: (pid: string, tid: string, claimer_id: string) =>
      http<ProjectTask>(`/api/projects/${pid}/tasks/${tid}/claim`, {
        method: "POST",
        body: JSON.stringify({ claimer_id }),
      }),
    release: (pid: string, tid: string, releaser_id: string) =>
      http<ProjectTask>(`/api/projects/${pid}/tasks/${tid}/release`, {
        method: "POST",
        body: JSON.stringify({ releaser_id }),
      }),
    close: (pid: string, tid: string, closed_by: string, reason?: string) =>
      http<ProjectTask>(`/api/projects/${pid}/tasks/${tid}/close`, {
        method: "POST",
        body: JSON.stringify({ closed_by, reason }),
      }),
    update: (pid: string, tid: string, patch: Partial<ProjectTask>) =>
      http<ProjectTask>(`/api/projects/${pid}/tasks/${tid}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    listComments: (pid: string, tid: string) =>
      http<{ items: ProjectComment[] }>(`/api/projects/${pid}/tasks/${tid}/comments`).then((r) => r.items),
    addComment: (
      pid: string,
      tid: string,
      input: { body: string; author_id: string; replies_to_comment_id?: string },
    ) =>
      http<ProjectComment>(`/api/projects/${pid}/tasks/${tid}/comments`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    listRelationships: (pid: string, tid: string, direction: "from" | "to" = "from") =>
      http<{ items: ProjectRelationship[] }>(
        `/api/projects/${pid}/tasks/${tid}/relationships?direction=${direction}`,
      ).then((r) => r.items),
    addRelationship: (
      pid: string,
      tid: string,
      input: { to_task_id: string; kind: string; created_by: string },
    ) =>
      http<ProjectRelationship>(`/api/projects/${pid}/tasks/${tid}/relationships`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    getContext: (tid: string) =>
      http<TaskContext>(`/api/projects/tasks/${tid}/context`),
  },

  elements: {
    list: (pid: string) =>
      http<{ items: ProjectElement[] }>(`/api/projects/${pid}/elements`).then((r) => r.items),
    get: (pid: string, eid: string) =>
      http<ProjectElement>(`/api/projects/${pid}/elements/${eid}`),
    create: (
      pid: string,
      input: {
        name: string;
        slug?: string;
        type?: ElementType;
        description?: string;
        assignee_id?: string | null;
        settings?: Record<string, unknown>;
      },
    ) =>
      http<ProjectElement>(`/api/projects/${pid}/elements`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    update: (
      pid: string,
      eid: string,
      patch: {
        name?: string;
        type?: ElementType;
        description?: string;
        assignee_id?: string | null;
        settings?: Record<string, unknown>;
      },
    ) =>
      http<ProjectElement>(`/api/projects/${pid}/elements/${eid}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    archive: (pid: string, eid: string) =>
      http<ProjectElement>(`/api/projects/${pid}/elements/${eid}/archive`, { method: "POST" }),
    remove: (pid: string, eid: string, mode?: "untag") =>
      http<{ ok: boolean }>(
        `/api/projects/${pid}/elements/${eid}${mode ? `?mode=${mode}` : ""}`,
        { method: "DELETE" },
      ),
  },

  routines: {
    list: (pid: string) =>
      http<{ items: Routine[] }>(`/api/projects/${pid}/routines`).then((r) => r.items),
    create: (
      pid: string,
      input: {
        title: string;
        body_template?: string;
        assignee_id?: string | null;
        trigger_kind: "cron" | "webhook" | "api";
        cron_expr?: string | null;
        enabled?: boolean;
      },
    ) =>
      http<Routine>(`/api/projects/${pid}/routines`, {
        method: "POST",
        body: JSON.stringify(input),
      }),
    update: (
      pid: string,
      rid: string,
      patch: {
        title?: string;
        body_template?: string;
        assignee_id?: string | null;
        cron_expr?: string | null;
        enabled?: boolean;
      },
    ) =>
      http<Routine>(`/api/projects/${pid}/routines/${rid}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      }),
    remove: (pid: string, rid: string) =>
      http<{ ok: boolean }>(`/api/projects/${pid}/routines/${rid}`, { method: "DELETE" }),
    trigger: (pid: string, rid: string) =>
      http<{ ok: boolean; task: ProjectTask }>(`/api/projects/${pid}/routines/${rid}/trigger`, {
        method: "POST",
      }),
  },

  activity: (pid: string) =>
    http<{ items: ProjectActivity[] }>(`/api/projects/${pid}/activity`).then((r) => r.items),

  subscribeEvents(projectId: string, onEvent: (ev: ProjectEvent) => void): () => void {
    const es = new EventSource(`/api/projects/${projectId}/events`);
    es.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data) as ProjectEvent); } catch { /* heartbeat / malformed — skip */ }
    };
    return () => es.close();
  },
};
