.pragma library
// Pure functions shared by the native apps. Each one mirrors a function of the
// web app and cites it; none of them decides anything on the controller's behalf.

// QML hands lists over as sequence wrappers (Array.isArray is false). Make a real array of anything list-like.
function arr(v) {
    if (v === null || v === undefined) return []
    if (Array.isArray(v)) return v
    if (typeof v === "object" && typeof v.length === "number") {
        var out = []
        for (var i = 0; i < v.length; i++) out.push(v[i])
        return out
    }
    return []
}

// desktop/src/apps/DecisionsApp.tsx:110-114: epoch seconds or milliseconds, or an ISO string.
function toMillis(ts) {
    if (typeof ts === "number")
        return ts < 1e12 ? ts * 1000 : ts
    if (typeof ts === "string" && ts.length > 0) {
        var p = Date.parse(ts)
        return isNaN(p) ? Date.now() : p
    }
    return Date.now()
}

// DecisionsApp.tsx:116-125 and NotificationsApp.tsx formatTime.
function relativeTime(ts, nowMs) {
    var now = nowMs === undefined ? Date.now() : nowMs
    var mins = Math.floor((now - toMillis(ts)) / 60000)
    if (mins < 1) return "just now"
    if (mins < 60) return mins + "m ago"
    var hrs = Math.floor(mins / 60)
    if (hrs < 24) return hrs + "h ago"
    return Math.floor(hrs / 24) + "d ago"
}

// desktop/src/lib/slug.ts: slugifyClient and slugifyWithFallback.
function slugifyClient(name) {
    var s = String(name).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase()
        .replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 63).replace(/-+$/, "")
    return s
}
function fnv1a32(s) {
    var h = 0x811c9dc5
    for (var i = 0; i < s.length; i++) {
        h ^= s.charCodeAt(i)
        // Math.imul is available in the QML JS engine
        h = Math.imul(h, 0x01000193)
    }
    var hex = (h >>> 0).toString(16)
    while (hex.length < 8) hex = "0" + hex
    return hex
}
function slugifyWithFallback(name, prefix) {
    return slugifyClient(name) || (prefix + "-" + fnv1a32(String(name)))
}

// ConsentActions.tsx:80-90
function computeScopeDiff(requested, granted) {
    return {
        dropped: requested.filter(function (s) { return granted.indexOf(s) < 0 }),
        added: granted.filter(function (s) { return requested.indexOf(s) < 0 })
    }
}

// ConsentActions.tsx:208-232: every entry must be a string, else the answer is malformed.
function projectScopesFrom(value) {
    var raw = value ? value.project_scopes : undefined
    if (raw === null || raw === undefined || typeof raw !== "object" || typeof raw.length !== "number") return null
    var list = arr(raw)
    for (var i = 0; i < list.length; i++)
        if (typeof list[i] !== "string") return null
    return list
}

// ConsentActions.tsx:464-475
function consentPayload(data) {
    if (!data || typeof data.request_id !== "string") return null
    var raw = arr(data.requested_scopes)
    var scopes = []
    {
        for (var i = 0; i < raw.length; i++)
            if (typeof raw[i] === "string") scopes.push(raw[i])
    }
    return {
        requestId: data.request_id,
        scopes: scopes,
        projectId: typeof data.project_id === "string" ? data.project_id : "",
        canonicalId: typeof data.canonical_id === "string" ? data.canonical_id : "",
        humanDuration: typeof data.human_duration === "string" ? data.human_duration : "",
        harness: typeof data.framework === "string" ? data.framework : "",
        agent: typeof data.identity_claim === "string" ? data.identity_claim
             : typeof data.canonical_id === "string" ? data.canonical_id : ""
    }
}

// SignInActions.tsx:19-37: the same allowlist as ALLOWED_SIGNIN_HOSTS in tinyagentos/auth_broker.py.
var ALLOWED_SIGNIN_HOSTS = ["claude.ai", "console.anthropic.com"]
function allowedSignInUrl(raw) {
    if (typeof raw !== "string") return ""
    if (/[\s\\\u0000-\u001f]/.test(raw)) return ""
    var m = /^https:\/\/([^\/?#]*)([\/?#].*)?$/i.exec(raw)
    if (!m) return ""
    var authority = m[1]
    if (authority.indexOf("@") >= 0) return ""               // no user or password
    var hm = /^([^:]+)(?::(\d*))?$/.exec(authority)
    if (!hm) return ""
    if (hm[2] !== undefined && hm[2] !== "" && hm[2] !== "443") return ""
    var host = hm[1].toLowerCase()
    return ALLOWED_SIGNIN_HOSTS.indexOf(host) >= 0 ? host : ""
}
function signInPayload(data) {
    if (!data || typeof data.request_id !== "string" || typeof data.url !== "string") return null
    var host = allowedSignInUrl(data.url)
    if (!host) return null
    return {
        requestId: data.request_id, url: data.url, host: host,
        agent: typeof data.agent === "string" ? data.agent : "An agent",
        prompt: typeof data.prompt === "string" ? data.prompt : ""
    }
}

// ConsentActions.tsx:182-193 and 379-405: the exact path and body of a decision.
// Returns {path, body} where body is undefined for a plain deny (no body at all).
function consentRequest(approved, o) {
    var base = o.source === "agent_scope_requests"
        ? "/api/agents/registry/" + encodeURIComponent(o.canonicalId) + "/scope-requests/" + encodeURIComponent(o.requestId)
        : "/api/agents/auth-requests/" + encodeURIComponent(o.requestId)
    var path = base + "/" + (approved ? "approve" : "deny")
    var body
    if (approved) {
        body = { granted_scopes: o.granted }
        if (o.needsProject && o.projectId) body.project_id = o.projectId
        if (o.canLead && o.makeLead && o.projectId) body.is_lead = true
    } else if (o.denyReasonRequired) {
        body = { reason: String(o.denyReason).trim() }
    }
    return { path: path, body: body }
}

// DecisionsApp.tsx:496-509 answerLabel: option labels joined by ", ", plus " (note)".
function answerLabel(decision) {
    var a = decision.answer
    var value = a ? a.value : null
    if (value === null || value === undefined) return "-"
    var values = (typeof value === "object" && typeof value.length === "number") ? arr(value) : [value]
    var opts = arr(decision.options)
    var labels = values.map(function (v) {
        for (var j = 0; j < opts.length; j++)
            if (opts[j].value === v) return opts[j].label
        return v
    })
    labels = labels.filter(function (l) { return l !== "" })
    if (a.other_value) labels.push(a.other_value)          // an "Other" answer carries its text here
    var text = labels.join(", ")
    if (a.note) text = text + " (" + a.note + ")"
    return text
}

// tsk-mul5pa GateGrantDetails.tsx: the grant-bearing metadata fields of a gate decision.
var GATE_KINDS = {
    execution_gate: { title: "Lets an agent run an action", fields: [["agent_name", "Agent"], ["action_class", "Action class"], ["tool", "Tool"]] },
    delegation_gate: { title: "Delegates a task to another agent", fields: [["from_agent", "From"], ["to_agent", "To"], ["task_title", "Task"], ["task_id", "Task id"]] },
    device_pairing: { title: "Pairs a device with this server", fields: [["pair_request_id", "Pair request"], ["server_cert_fingerprint", "Server certificate"]] },
    app_grant: { title: "Grants an app permissions", fields: [["app_id", "App"], ["capabilities", "Capabilities"]] },
    project_create: { title: "Creates a project", fields: [["requested_name", "Name"], ["requested_slug", "Slug"], ["from_agent", "Requested by"]] },
    collab_delegation_gate: { title: "Invites a collaborator to a project", fields: [["contact_id", "Contact"], ["project_id", "Project"], ["granted_scopes", "Scopes"], ["elevated_scopes", "Elevated scopes"]] }
}
function gateDetails(metadata) {
    if (!metadata || typeof metadata.kind !== "string") return null
    if (!Object.prototype.hasOwnProperty.call(GATE_KINDS, metadata.kind)) return null
    var g = GATE_KINDS[metadata.kind], rows = []
    for (var i = 0; i < g.fields.length; i++) {
        var v = metadata[g.fields[i][0]], text
        if (v !== null && typeof v === "object" && typeof v.length === "number") text = arr(v).map(String).join(", ")
        else if (v === null || v === undefined) text = ""
        else text = typeof v === "object" ? JSON.stringify(v) : String(v)
        if (text !== "") rows.push({ label: g.fields[i][1], value: text })
    }
    return { title: g.title, rows: rows }
}
