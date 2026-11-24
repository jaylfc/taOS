import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos 1.0
import Taos.Style 1.0
import Taos.Apps 1.0

// Decisions: the human-in-the-loop inbox. Agents ask, the user answers once.
// Pending external-agent access requests and scope requests sit above the
// questions and use the same consent component as the Notifications app. All
// state is the controller's; this window holds only unsent text.
TaosWindow {
    id: win
    width: 640
    height: 760
    minimumWidth: 420
    minimumHeight: 460
    title: qsTr("Decisions")
    objectName: "decisionsWindow"

    readonly property bool signedIn: Taos.session.state === Session.SignedIn
    property int tab: 0                         // 0 pending, 1 archive, 2 withdrawn
    property bool pendingLoaded: false
    readonly property int pendingCount: pendingQ.model.count
    readonly property int accessCount: accessQ.model.count
    readonly property int scopeCount: scopeQ.model.count
    readonly property bool stale: signedIn && pendingLoaded && !pendingQ.live
    connectionText: Taos.client.connectionState === Client.Reconnecting ? qsTr("Can't reach the controller. Retrying.") : ""

    readonly property var decisionRoles: ["id", "from_agent", "project_id", "question", "type", "options", "context", "priority",
        "status", "answer", "created_at", "answered_at", "notes", "metadata", "parent_decision_id", "withdraw_reason",
        "withdrawn_at", "withdrawn_by"]
    // Decisions raised by an agent arrive with a notification; answers, notes and withdrawals have their own kinds.
    readonly property var decisionKinds: ["decision.answered", "decision.note", "decision.withdrawn", "notification.added"]

    // Launch argument from the shell's open-app request: --tab=pending|archive|withdrawn
    Component.onCompleted: {
        const args = Qt.application.arguments
        for (let i = 0; i < args.length; i++) {
            if (args[i].indexOf("--tab=") !== 0) continue
            const at = ["pending", "archive", "withdrawn"].indexOf(args[i].substring(6))
            if (at >= 0) tab = at
        }
    }

    function refreshAll() {
        pendingQ.refresh(); accessQ.refresh(); scopeQ.refresh()
        if (tab === 1) answeredQ.refresh()
        if (tab === 2) withdrawnQ.refresh()
    }
    onActiveChanged: if (active && signedIn) refreshAll()      // silent refresh on window focus

    LiveQuery {
        id: pendingQ
        objectName: "pendingQuery"
        op: "decisions.list"
        args: ({ status: "pending" })
        eventKinds: win.decisionKinds
        roles: win.decisionRoles
        running: win.signedIn
        onLoaded: win.pendingLoaded = true
    }
    LiveQuery {
        id: answeredQ
        objectName: "answeredQuery"
        op: "decisions.list"
        args: ({ status: "answered" })
        eventKinds: win.decisionKinds
        roles: win.decisionRoles
        running: win.signedIn && win.tab === 1
    }
    LiveQuery {
        id: withdrawnQ
        objectName: "withdrawnQuery"
        op: "decisions.list"
        args: ({ status: "withdrawn" })
        eventKinds: win.decisionKinds
        roles: win.decisionRoles
        running: win.signedIn && win.tab === 2
    }
    // GET /api/agents/auth-requests?status=pending -> {requests: [...]}   (admin; a 403 leaves the list empty)
    LiveQuery {
        id: accessQ
        objectName: "accessQuery"
        op: "http"
        args: ({ method: "GET", path: "/api/agents/auth-requests", query: { status: "pending" } })
        resultKey: "requests"
        eventKinds: ["notification.added", "decision.answered"]
        roles: ["id", "identity_claim", "framework", "requested_scopes", "reason", "project_id", "human_duration"]
        running: win.signedIn
    }
    // GET /api/agents/scope-requests?status=pending -> {requests: [...]}
    LiveQuery {
        id: scopeQ
        objectName: "scopeQuery"
        op: "http"
        args: ({ method: "GET", path: "/api/agents/scope-requests", query: { status: "pending" } })
        resultKey: "requests"
        eventKinds: ["notification.added", "decision.answered"]
        roles: ["id", "canonical_id", "agent_display_name", "requested_scopes", "project_id", "reason", "created_ts"]
        running: win.signedIn
    }

    // After an answer the focus moves to the next pending card (the one now at the same place).
    property int focusAfterIndex: -1
    Connections {
        target: pendingQ
        function onLoaded() {
            if (win.focusAfterIndex < 0) return
            const i = Math.min(win.focusAfterIndex, decisionRepeater.count - 1)
            win.focusAfterIndex = -1
            Qt.callLater(() => { const it = i >= 0 ? decisionRepeater.itemAt(i) : null; if (it) it.focusFirst() })
        }
    }

    Backdrop { anchors.fill: parent }
    SignInForm { anchors.fill: parent; visible: !win.signedIn; appName: qsTr("Decisions") }

    ColumnLayout {
        anchors.fill: parent
        anchors.topMargin: win.connectionText.length > 0 ? 32 : 0
        visible: win.signedIn
        spacing: 0

        // ---- header and tabs ---------------------------------------------------------
        GlassPanel {
            Layout.fillWidth: true
            Layout.preferredHeight: head.implicitHeight + 28
            strong: true
            radius: 0
            edgeTop: false
            border.width: 0
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Look.hairline }
            ColumnLayout {
                id: head
                anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 16 }
                spacing: 12
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Glyph { name: "inbox"; size: 18; color: Look.textSecondary }
                    TaosText {
                        text: qsTr("Decisions")
                        font.pixelSize: Look.heading - 2
                        font.weight: Font.DemiBold
                        Accessible.ignored: false
                        Accessible.role: Accessible.Heading
                        Accessible.name: text
                    }
                    Rectangle {
                        objectName: "pendingBadge"
                        visible: win.pendingCount > 0
                        implicitWidth: Math.max(20, badgeText.implicitWidth + 12)
                        height: 20; radius: 10
                        color: Look.raised
                        border.width: 1; border.color: Look.hairline
                        Accessible.role: Accessible.StaticText
                        Accessible.name: qsTr("%1 pending").arg(win.pendingCount)
                        TaosText { id: badgeText; anchors.centerIn: parent; text: win.pendingCount
                                   font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.text }
                    }
                    Item { Layout.fillWidth: true }
                    Chip {
                        objectName: "pausedChip"
                        visible: win.stale
                        text: qsTr("paused")
                        tone: "warning"
                        accessibleName: qsTr("live updates paused")
                    }
                }
                C.TabBar {
                    id: tabs
                    objectName: "tabBar"
                    Layout.fillWidth: true
                    currentIndex: win.tab
                    onCurrentIndexChanged: win.tab = currentIndex
                    background: Rectangle { radius: 9; color: Look.field; border.width: 1; border.color: Look.hairline }
                    spacing: 2
                    padding: 2
                    component SegTab: C.TabButton {
                        id: seg
                        implicitHeight: 28
                        hoverEnabled: true
                        focusPolicy: Qt.StrongFocus
                        Accessible.role: Accessible.PageTab
                        Accessible.name: text
                        background: Rectangle {
                            radius: 7
                            color: seg.checked ? Look.raised : seg.hovered ? Look.hover : "transparent"
                            border.width: seg.visualFocus ? 2 : seg.checked ? 1 : 0
                            border.color: seg.visualFocus ? Look.focusRing : Look.glassStroke
                            Behavior on color { ColorAnimation { duration: Look.fast } }
                        }
                        contentItem: TaosText {
                            text: seg.text
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                            font.pixelSize: Look.body
                            font.weight: seg.checked ? Font.DemiBold : Font.Medium
                            color: seg.checked ? Look.text : Look.textSecondary
                        }
                    }
                    SegTab { objectName: "tabPending"; text: win.pendingCount > 0 ? qsTr("Pending (%1)").arg(win.pendingCount) : qsTr("Pending") }
                    SegTab { objectName: "tabArchive"; text: qsTr("Archive") }
                    SegTab { objectName: "tabWithdrawn"; text: qsTr("Withdrawn") }
                }
            }
        }

        // ---- pages ---------------------------------------------------------------------
        StackLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: win.tab

            // Pending: access requests, scope requests, then decisions
            Item {
                Flickable {
                    id: pendingFlick
                    anchors.fill: parent
                    contentWidth: width
                    contentHeight: pendingCol.implicitHeight + 28
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Look.scrollbar; opacity: parent.active ? 1 : 0 } }
                    visible: win.pendingCount + win.accessCount + win.scopeCount > 0
                    ColumnLayout {
                        id: pendingCol
                        x: 14; y: 14
                        width: pendingFlick.width - 28
                        spacing: 10

                        TaosText {
                            visible: win.accessCount > 0
                            text: qsTr("Access requests (%1)").arg(win.accessCount)
                            font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textSecondary
                            Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                        }
                        Repeater {
                            id: accessRepeater
                            model: accessQ.model
                            delegate: RequestCards {
                                Layout.fillWidth: true
                                kind: "access"
                                onResolved: { accessQ.refresh(); pendingQ.refresh() }
                                onRefetchRequested: accessQ.refresh()    // another approver may have decided it
                            }
                        }
                        TaosText {
                            visible: win.scopeCount > 0
                            text: qsTr("Scope requests (%1)").arg(win.scopeCount)
                            font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textSecondary
                            Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                        }
                        Repeater {
                            id: scopeRepeater
                            model: scopeQ.model
                            delegate: RequestCards {
                                Layout.fillWidth: true
                                kind: "scope"
                                onResolved: { scopeQ.refresh(); pendingQ.refresh() }
                                onRefetchRequested: scopeQ.refresh()
                            }
                        }
                        TaosText {
                            visible: win.pendingCount > 0 && (win.accessCount > 0 || win.scopeCount > 0)
                            text: qsTr("Questions (%1)").arg(win.pendingCount)
                            font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textSecondary
                            Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
                        }
                        Repeater {
                            id: decisionRepeater
                            objectName: "decisionRepeater"
                            model: pendingQ.model
                            delegate: DecisionCard {
                                required property int index
                                Layout.fillWidth: true
                                onAnswered: (id) => { win.focusAfterIndex = index; pendingQ.refresh() }
                                onChanged: pendingQ.refresh()
                            }
                        }
                    }
                }
                TaosText {
                    visible: !win.pendingLoaded && pendingQ.errorText === ""
                    anchors.centerIn: parent
                    text: qsTr("Loading...")
                    color: Look.textTertiary
                }
                EmptyState {
                    objectName: "emptyPending"
                    anchors.centerIn: parent
                    visible: win.pendingLoaded && win.pendingCount + win.accessCount + win.scopeCount === 0 && pendingQ.errorText === ""
                    glyph: "inbox"
                    title: qsTr("No decisions waiting on you.")
                    detail: qsTr("When an agent needs an answer or access, it appears here.")
                }
                ColumnLayout {
                    objectName: "errorState"
                    anchors.centerIn: parent
                    spacing: 12
                    visible: pendingQ.errorText !== "" && win.pendingCount + win.accessCount + win.scopeCount === 0
                    EmptyState { glyph: "alert"; tone: "error"; title: qsTr("Can't load decisions"); detail: pendingQ.errorText
                                 Layout.alignment: Qt.AlignHCenter }
                    TaosButton { Layout.alignment: Qt.AlignHCenter; text: qsTr("Try again"); glyph: "history"; onClicked: win.refreshAll() }
                }
                // a failed refresh never blanks the list: say so and keep what is there
                Rectangle {
                    visible: pendingQ.errorText !== "" && win.pendingCount > 0
                    anchors { top: parent.top; horizontalCenter: parent.horizontalCenter; topMargin: 6 }
                    radius: 12; height: 24; width: staleNote.implicitWidth + 24
                    color: Look.tint(Look.warning, 0.2); border.width: 1; border.color: Look.tint(Look.warning, 0.4)
                    TaosText { id: staleNote; anchors.centerIn: parent; text: qsTr("Could not refresh. Showing the last list.")
                               font.pixelSize: Look.caption; color: Look.warning }
                }
            }

            // Archive
            Item {
                Flickable {
                    id: answeredFlick
                    objectName: "answeredList"
                    anchors.fill: parent
                    clip: true
                    readonly property int count: answeredRepeater.count
                    visible: count > 0
                    contentWidth: width
                    contentHeight: answeredCol.implicitHeight + 28
                    boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Look.scrollbar; opacity: parent.active ? 1 : 0 } }
                    ColumnLayout {
                        id: answeredCol
                        x: 14; y: 14
                        width: answeredFlick.width - 28
                        spacing: 10
                        Repeater { id: answeredRepeater; model: answeredQ.model; delegate: ArchiveCard { Layout.fillWidth: true } }
                    }
                }
                EmptyState { objectName: "emptyArchive"; anchors.centerIn: parent
                             visible: answeredFlick.count === 0 && !answeredQ.loading
                             glyph: "check"; title: qsTr("No answered decisions yet.") }
            }

            // Withdrawn
            Item {
                Flickable {
                    id: withdrawnFlick
                    objectName: "withdrawnList"
                    anchors.fill: parent
                    clip: true
                    readonly property int count: withdrawnRepeater.count
                    visible: count > 0
                    contentWidth: width
                    contentHeight: withdrawnCol.implicitHeight + 28
                    boundsBehavior: Flickable.StopAtBounds
                    C.ScrollBar.vertical: C.ScrollBar { policy: C.ScrollBar.AsNeeded; contentItem: Rectangle { implicitWidth: 6; radius: 3; color: Look.scrollbar; opacity: parent.active ? 1 : 0 } }
                    ColumnLayout {
                        id: withdrawnCol
                        x: 14; y: 14
                        width: withdrawnFlick.width - 28
                        spacing: 10
                        Repeater { id: withdrawnRepeater; model: withdrawnQ.model; delegate: ArchiveCard { Layout.fillWidth: true } }
                    }
                }
                EmptyState { objectName: "emptyWithdrawn"; anchors.centerIn: parent
                             visible: withdrawnFlick.count === 0 && !withdrawnQ.loading
                             glyph: "x"; title: qsTr("No withdrawn decisions.") }
            }
        }
    }
}
