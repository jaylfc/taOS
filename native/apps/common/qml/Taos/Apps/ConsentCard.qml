import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import "Logic.js" as Logic

// The consent block: Allow or Deny for an agent access request or scope request.
// It is AUTHORISATION, so it decides nothing itself. It reads the server's scope
// vocabulary, and the server's project list, and POSTs exactly what
// desktop/src/components/ConsentActions.tsx POSTs (the path and body are built by
// Logic.consentRequest, which cites the web lines). What is granted is shown
// before Allow: the agent, the scopes (with every dropped or added scope called
// out), the project the grant binds to (or that it is OS-wide) and its duration.
//
// Used by the Notifications rows and the Decisions access / scope request cards.
Item {
    id: root

    property string requestId: ""
    property var scopes: []
    /// Scopes to grant. Null: all of `scopes`. Ignored when `selectable`.
    property var grantedScopes: null
    property string requestedProjectId: ""
    property string source: "auth_requests"      // auth_requests | agent_scope_requests
    property string canonicalId: ""
    property string humanDuration: ""
    property string harness: ""
    property string agentLabel: ""
    property bool denyReasonRequired: false
    property bool showEffectiveBinding: false
    /// A checkbox per scope: unticking narrows the grant, it can never widen it.
    property bool selectable: false

    /// The request was decided (or the lead is unconfirmed and the user dismissed it).
    signal resolved()
    /// A call failed: the owner should refetch the request, which another approver may have decided.
    signal refetchRequested()

    // ---- state ---------------------------------------------------------------------
    property var ticked: []
    property bool busy: false
    property string errorText: ""
    property var projectScopes: null              // null until the server's vocabulary is read
    property string vocabError: ""
    property bool loadingVocab: false
    property var projects: []
    property bool loadingProjects: false
    property string selectedProjectId: ""
    property var resolvedRequested: null
    property bool requestedNotFound: false
    property bool creating: false
    property string newName: ""
    property bool makeLead: false
    property bool leadUnconfirmed: false
    property bool denying: false
    property string denyReason: ""
    property int vocabToken: 0
    property int projectToken: 0
    readonly property int denyReasonMax: 2000      // ConsentActions.tsx:54

    // Lists reach QML as sequence wrappers, not JS arrays: normalise once.
    readonly property var scopeList: Logic.arr(scopes)
    readonly property var granted: selectable
        ? scopeList.filter(function (s) { return ticked.indexOf(s) >= 0 })
        : (grantedScopes !== null ? Logic.arr(grantedScopes) : scopeList)
    readonly property var diff: Logic.computeScopeDiff(scopeList, granted)
    readonly property bool needsProject: projectScopes !== null
        && granted.some(function (s) { return projectScopes.indexOf(s) >= 0 })
    readonly property bool canLead: source === "auth_requests" && needsProject
        && granted.some(function (s) { return s === "project_tasks" || s.indexOf("canvas_") === 0 })
    readonly property string pickedProjectName: creating ? newName.trim() : projectName(selectedProjectId)
    readonly property string requestedProjectName: resolvedRequested ? resolvedRequested.name : ""
    readonly property string allowLabel: "Allow " + granted.join(", ")
        + (needsProject && pickedProjectName ? " on " + pickedProjectName : "")
    readonly property string denyLabel: "Deny " + scopeList.join(", ")
        + (needsProject && requestedProjectName ? " on " + requestedProjectName : "")
    readonly property bool allowDisabled: busy || projectScopes === null
        || (scopeList.length > 0 && granted.length === 0)
        || (needsProject && (creating ? newName.trim() === "" : selectedProjectId === ""))

    implicitHeight: col.implicitHeight
    implicitWidth: col.implicitWidth

    Accessible.role: Accessible.Grouping
    Accessible.name: "Consent actions"

    function projectName(id) {
        for (var i = 0; i < projects.length; i++)
            if (projects[i].id === id) return projects[i].name
        return ""
    }

    property string scopesKey: ""
    function resetTicked() { ticked = scopeList.slice() }
    // A refetch hands over an equal list as a new array: only a real change resets the ticks.
    onScopesChanged: {
        var k = JSON.stringify(Logic.arr(scopes))
        if (k !== scopesKey) { scopesKey = k; resetTicked() }
    }
    Component.onCompleted: { resetTicked(); loadVocab() }
    Component.onDestruction: { vocabToken++; projectToken++ }
    onNeedsProjectChanged: if (needsProject) loadProjects()
    onRequestedProjectIdChanged: if (needsProject) loadProjects()

    Connections {
        target: Taos.session
        function onStateChanged() {
            if (Taos.session.state === Session.SignedIn && root.projectScopes === null && !root.loadingVocab)
                root.loadVocab()
        }
    }

    // ---- reads (ConsentActions.tsx:205-297) ----------------------------------------
    Timer {
        id: vocabTimer
        interval: 10000                           // VOCAB_TIMEOUT_MS, ConsentActions.tsx:59
        onTriggered: {
            root.vocabToken++
            root.loadingVocab = false
            root.projectScopes = null
            root.vocabError = "no answer within 10s"
        }
    }
    function loadVocab() {
        var tok = ++vocabToken
        loadingVocab = true
        vocabError = ""
        vocabTimer.restart()
        // GET /api/agents/scope-vocabulary: which scopes are project-bound is the server's answer.
        Taos.http("GET", "/api/agents/scope-vocabulary", {}, null, function (ok, value, err) {
            if (!root || tok !== root.vocabToken) return
            vocabTimer.stop()
            root.loadingVocab = false
            if (!ok) { root.projectScopes = null; root.vocabError = err || "the request failed"; return }
            var list = Logic.projectScopesFrom(value)
            if (list === null) { root.projectScopes = null; root.vocabError = "the server's answer was malformed"; return }
            root.projectScopes = list
            root.vocabError = ""
        })
    }
    function loadProjects() {
        var tok = ++projectToken
        loadingProjects = true
        requestedNotFound = false
        resolvedRequested = null
        // GET /api/projects?status=active
        Taos.http("GET", "/api/projects", { status: "active" }, null, function (ok, value, err) {
            if (!root || tok !== root.projectToken) return
            root.loadingProjects = false
            var items = ok && value ? Logic.arr(value.items) : []
            root.projects = items
            if (root.requestedProjectId) {
                var match = null
                for (var i = 0; i < items.length; i++)
                    if (items[i].id === root.requestedProjectId) match = items[i]
                if (match) {
                    root.resolvedRequested = match
                    root.requestedNotFound = false
                    if (root.selectedProjectId === "") root.selectedProjectId = match.id
                } else {
                    // Never adopt an id the operator cannot see: leave the picker blank and say so.
                    root.resolvedRequested = null
                    root.requestedNotFound = true
                }
            } else {
                root.resolvedRequested = null
                root.requestedNotFound = false
                if (root.selectedProjectId === "" && items.length === 1) root.selectedProjectId = items[0].id
            }
        })
    }

    // ---- writes (ConsentActions.tsx:299-441) ----------------------------------------
    function createProject(cb) {
        var name = newName.trim()
        if (name === "") { cb(""); return }
        // POST /api/projects {name, slug}
        Taos.http("POST", "/api/projects", {}, { name: name, slug: Logic.slugifyWithFallback(name, "project") },
                  function (ok, value, err) {
            if (!ok) { root.errorText = err || "Could not create project"; cb(""); return }
            var id = value && typeof value.id === "string" ? value.id : ""
            if (id === "") {
                root.errorText = "Project created but the response was unexpected; reload and pick it from the list."
                cb("")
                return
            }
            root.projects = [{ id: id, name: typeof value.name === "string" ? value.name : id }].concat(root.projects)
            root.selectedProjectId = id
            root.creating = false
            root.newName = ""
            cb(id)
        })
    }

    function decide(approved) {
        busy = true
        errorText = ""
        if (approved && projectScopes === null) {
            errorText = vocabError || "Still reading the scope vocabulary from the server; try again in a moment."
            busy = false
            return
        }
        if (approved && needsProject && creating && newName.trim() !== "") {
            createProject(function (id) {
                if (id === "") { busy = false; return }
                send(true, id)
            })
            return
        }
        if (approved && needsProject && selectedProjectId === "") {
            errorText = "Select or create a project for the requested project access."
            busy = false
            return
        }
        if (source === "agent_scope_requests" && canonicalId === "") {
            errorText = "Missing agent identifier; cannot process this scope request."
            busy = false
            return
        }
        if (!approved && denyReasonRequired && denyReason.trim() === "") {
            errorText = "Give a reason for denying this request."
            busy = false
            return
        }
        send(approved, selectedProjectId)
    }

    function send(approved, projectId) {
        var req = Logic.consentRequest(approved, {
            source: source, canonicalId: canonicalId, requestId: requestId, granted: granted,
            needsProject: needsProject, projectId: projectId, canLead: canLead, makeLead: makeLead,
            denyReasonRequired: denyReasonRequired, denyReason: denyReason
        })
        var wantsLead = approved && req.body && req.body.is_lead === true
        Taos.http("POST", req.path, {}, req.body === undefined ? null : req.body, function (ok, value, err) {
            if (!root) return
            if (!ok) {
                root.errorText = err || "Request failed"
                root.busy = false
                root.refetchRequested()
                return
            }
            if (wantsLead && !(value && value.lead_assigned === true)) {
                root.leadUnconfirmed = true
                root.busy = false
                return
            }
            root.resolved()
        })
    }

    // ---- face ------------------------------------------------------------------------
    ColumnLayout {
        id: col
        width: parent.width
        spacing: 10

        TaosText {
            visible: root.vocabError !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Look.danger
            font.pixelSize: Look.small
            text: "Could not confirm which scopes need a project (" + root.vocabError
                  + "), so this request cannot be approved safely."
            Accessible.ignored: false
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
        }
        TaosButton {
            visible: root.vocabError !== ""
            compact: true
            text: "Retry"
            glyph: "history"
            onClicked: root.loadVocab()
        }

        // What approving grants.
        Rectangle {
            Layout.fillWidth: true
            visible: root.scopeList.length > 0 || root.agentLabel !== ""
            implicitHeight: review.implicitHeight + 20
            radius: Look.radiusControl + 2
            color: Look.field
            border.width: 1
            border.color: Look.hairline
            ColumnLayout {
                id: review
                anchors { left: parent.left; right: parent.right; top: parent.top; margins: 10 }
                spacing: 8

                TaosText {
                    text: "WHAT APPROVING GRANTS"
                    font.pixelSize: Look.caption - 1
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.8
                    color: Look.textTertiary
                }
                GridLayout {
                    Layout.fillWidth: true
                    columns: 2
                    columnSpacing: 12
                    rowSpacing: 6
                    TaosText { visible: root.agentLabel !== ""; text: "Agent"; color: Look.textTertiary; font.pixelSize: Look.small
                               Layout.alignment: Qt.AlignTop }
                    TaosText { visible: root.agentLabel !== ""; text: root.agentLabel; font.weight: Font.Medium
                               Layout.fillWidth: true; wrapMode: Text.WrapAnywhere }
                    TaosText { visible: root.scopeList.length > 0; text: "Requested"; color: Look.textTertiary; font.pixelSize: Look.small
                               Layout.alignment: Qt.AlignTop; topPadding: 2 }
                    Flow {
                        visible: root.scopeList.length > 0
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            model: root.scopeList
                            ScopeBadge { required property string modelData
                                         scope: modelData
                                         kind: root.diff.dropped.indexOf(modelData) >= 0 ? "dropped" : "normal" }
                        }
                    }
                    TaosText { visible: root.scopeList.length > 0; text: "Granted"; color: Look.textTertiary; font.pixelSize: Look.small
                               Layout.alignment: Qt.AlignTop; topPadding: 2 }
                    Flow {
                        visible: root.scopeList.length > 0
                        Layout.fillWidth: true
                        spacing: 4
                        Repeater {
                            model: root.granted
                            ScopeBadge { required property string modelData
                                         scope: modelData
                                         kind: root.diff.added.indexOf(modelData) >= 0 ? "added" : "normal" }
                        }
                        TaosText { visible: root.granted.length === 0; text: "nothing"; color: Look.textTertiary
                                   font.pixelSize: Look.small }
                    }
                    TaosText { visible: root.humanDuration !== ""; text: "Duration"; color: Look.textTertiary; font.pixelSize: Look.small }
                    TaosText { visible: root.humanDuration !== ""; text: root.humanDuration; Layout.fillWidth: true; wrapMode: Text.Wrap }
                }
                TaosText {
                    visible: root.diff.dropped.length > 0 || root.diff.added.length > 0
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    color: Look.danger
                    font.pixelSize: Look.small
                    text: (root.diff.dropped.length > 0 ? "Dropping " + root.diff.dropped.length + " requested scope(s): "
                           + root.diff.dropped.join(", ") + "." : "")
                          + (root.diff.added.length > 0 ? " Granting " + root.diff.added.length + " scope(s) beyond the request: "
                             + root.diff.added.join(", ") + "." : "")
                    Accessible.ignored: false
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: text
                }
            }
        }

        // Per-scope checkboxes (scope requests): untick to narrow.
        ColumnLayout {
            visible: root.selectable && root.scopeList.length > 0
            Layout.fillWidth: true
            spacing: 2
            Accessible.role: Accessible.Grouping
            Accessible.name: "Scopes to grant"
            TaosText { text: "Scopes to grant"; font.pixelSize: Look.small; color: Look.textTertiary }
            Flow {
                Layout.fillWidth: true
                spacing: 14
                Repeater {
                    model: root.selectable ? root.scopeList : []
                    TaosCheck {
                        required property string modelData
                        text: modelData
                        mono: true
                        accessibleName: "Grant " + modelData
                        enabled: !root.busy
                        checked: root.ticked.indexOf(modelData) >= 0
                        onToggled: {
                            var cur = root.ticked.slice()
                            var at = cur.indexOf(modelData)
                            if (at >= 0) cur.splice(at, 1); else cur.push(modelData)
                            // keep the request's order; a scope outside the request can never be added
                            root.ticked = root.scopeList.filter(function (s) { return cur.indexOf(s) >= 0 })
                        }
                    }
                }
            }
        }

        TaosText {
            visible: root.harness === "grok"
            text: "This token will be readable by all bots on this Grok account."
            color: Look.warning
            font.pixelSize: Look.small
            Layout.fillWidth: true
            wrapMode: Text.Wrap
        }
        // ConsentActions.tsx:~440 tsk-6ghcrv: say where the grant lands.
        TaosText {
            visible: root.showEffectiveBinding && root.projectScopes !== null && !root.needsProject && root.granted.length > 0
            text: "Grants OS-wide (all projects)"
            color: Look.danger
            font.weight: Font.Medium
            font.pixelSize: Look.small
        }

        // Project picker.
        ColumnLayout {
            visible: root.needsProject
            Layout.fillWidth: true
            spacing: 6
            TaosText {
                visible: root.requestedProjectId !== "" && root.requestedNotFound
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Look.danger
                font.pixelSize: Look.small
                text: "Requested project " + root.requestedProjectId
                      + " not found (not visible to you). Pick a project you can see below, or create one."
                Accessible.ignored: false
                Accessible.role: Accessible.AlertMessage
                Accessible.name: text
            }
            TaosText {
                visible: root.requestedProjectId !== "" && root.resolvedRequested !== null
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Look.textSecondary
                font.pixelSize: Look.small
                text: "Requesting access for " + (root.resolvedRequested ? root.resolvedRequested.name + " (" + root.resolvedRequested.id + ")" : "")
            }
            RowLayout {
                visible: !root.creating
                Layout.fillWidth: true
                spacing: 6
                TaosCombo {
                    id: projectPick
                    Layout.fillWidth: true
                    accessibleName: "Grant project access for"
                    enabled: !root.busy && !root.loadingProjects
                    invalid: root.requestedNotFound
                    textRole: "name"
                    valueRole: "id"
                    model: [{ id: "", name: root.loadingProjects ? "Loading..." : "Select a project" }].concat(root.projects)
                    currentIndex: {
                        for (var i = 0; i < model.length; i++)
                            if (model[i].id === root.selectedProjectId) return i
                        return 0
                    }
                    onActivated: (i) => { root.selectedProjectId = model[i].id; root.requestedNotFound = false }
                }
                TaosButton { text: "New"; compact: true; enabled: !root.busy
                             onClicked: { root.requestedNotFound = false; root.creating = true } }
            }
            RowLayout {
                visible: root.creating
                Layout.fillWidth: true
                spacing: 6
                TaosField {
                    Layout.fillWidth: true
                    placeholderText: "New project name"
                    accessibleName: "New project name"
                    text: root.newName
                    onTextEdited: root.newName = text
                }
                TaosButton { text: "Cancel"; compact: true; enabled: !root.busy
                             onClicked: { root.creating = false; root.newName = "" } }
            }
            TaosCheck {
                visible: root.canLead
                text: "Make project lead"
                enabled: !root.busy
                checked: root.makeLead
                onToggled: root.makeLead = checked
            }
        }

        // Decision buttons.
        RowLayout {
            visible: !root.leadUnconfirmed
            Layout.fillWidth: true
            spacing: 8
            TaosButton {
                text: "Allow"
                glyph: "check"
                variant: "success"
                accessibleName: root.allowLabel
                enabled: !root.allowDisabled
                onClicked: root.decide(true)
            }
            TaosButton {
                text: "Deny"
                glyph: "x"
                accessibleName: root.denyLabel
                enabled: !root.busy && !root.denying
                onClicked: {
                    if (root.denyReasonRequired) { root.denying = true; return }
                    root.decide(false)
                }
            }
            Item { Layout.fillWidth: true }
            BusyIndicatorDot { visible: root.busy }
        }
        ColumnLayout {
            visible: root.denying && !root.leadUnconfirmed
            Layout.fillWidth: true
            spacing: 6
            TaosArea {
                Layout.fillWidth: true
                rows: 2
                placeholderText: "Why is this request denied?"
                accessibleName: "Reason for denying"
                maximumLength: root.denyReasonMax
                onEdited: root.denyReason = text
            }
            RowLayout {
                spacing: 8
                TaosButton {
                    text: "Confirm deny"
                    variant: "danger"
                    enabled: !root.busy && root.denyReason.trim() !== ""
                    onClicked: root.decide(false)
                }
                TaosButton { text: "Cancel"; enabled: !root.busy; onClicked: { root.denying = false; root.denyReason = "" } }
            }
        }
        RowLayout {
            visible: root.leadUnconfirmed
            Layout.fillWidth: true
            spacing: 8
            TaosText {
                Layout.fillWidth: true
                wrapMode: Text.Wrap
                color: Look.warning
                font.pixelSize: Look.small
                text: "Approved, but the project lead could not be set. Make the agent lead from the project's Members."
                Accessible.ignored: false
                Accessible.role: Accessible.AlertMessage
                Accessible.name: text
            }
            TaosButton { text: "Dismiss"; compact: true; onClicked: root.resolved() }
        }
        TaosText {
            visible: root.errorText !== ""
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Look.danger
            font.pixelSize: Look.small
            text: root.errorText
            Accessible.ignored: false
            Accessible.role: Accessible.AlertMessage
            Accessible.name: text
        }
    }

    component BusyIndicatorDot: Rectangle {
        width: 8; height: 8; radius: 4
        color: Look.accent
        SequentialAnimation on opacity {
            running: visible && !Look.reduceMotion
            loops: Animation.Infinite
            NumberAnimation { to: 0.25; duration: 500 }
            NumberAnimation { to: 1; duration: 500 }
        }
    }
}
