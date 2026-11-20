import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0

// The two request cards of the Decisions inbox (DecisionsApp.tsx:657-783): an
// external agent asking for access, and a registered agent asking for more
// scopes. Both put their decision through the shared ConsentCard.
GlassPanel {
    id: root
    required property var model
    /// "access" or "scope"
    property string kind: "access"
    signal resolved()
    signal refetchRequested()

    readonly property var scopes: model.requested_scopes || []
    readonly property string display: (model.agent_display_name || "").trim()
    implicitHeight: col.implicitHeight + 28
    Accessible.role: Accessible.Grouping
    Accessible.name: kind === "access" ? qsTr("Access request from %1").arg(model.identity_claim)
                                       : qsTr("Scope request from %1").arg(model.canonical_id)

    function focusFirst() { consent.forceActiveFocus(Qt.TabFocusReason) }

    ColumnLayout {
        id: col
        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 14 }
        spacing: 8
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Chip { text: root.kind === "access" ? qsTr("Access request") : qsTr("Scope request")
                   tone: "warning"; glyph: root.kind === "access" ? "shield" : "key" }
            Chip { visible: root.kind === "access"; text: root.model.framework || "" }
            Chip { visible: root.kind === "scope"
                   text: root.model.project_id ? qsTr("Requested project %1").arg(root.model.project_id) : qsTr("No project requested") }
            Item { Layout.fillWidth: true }
            TaosText { visible: root.kind === "scope" && !!root.model.created_ts
                       text: Format.relativeTime(root.model.created_ts, Format.tick); font.pixelSize: Look.small; color: Look.textTertiary }
        }
        TaosText {
            Layout.fillWidth: true
            wrapMode: Text.WrapAnywhere
            font.pixelSize: Look.bodyLarge
            font.weight: Font.DemiBold
            text: root.kind === "access" ? root.model.identity_claim
                  : (root.display !== "" && root.display !== root.model.canonical_id ? root.display + "  " : "") + root.model.canonical_id
        }
        TaosText {
            visible: !!root.model.reason
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            font.italic: true
            color: Look.textSecondary
            text: "“" + (root.model.reason || "") + "”"
        }
        ConsentCard {
            id: consent
            Layout.fillWidth: true
            requestId: root.model.id
            scopes: root.scopes
            requestedProjectId: root.model.project_id || ""
            source: root.kind === "access" ? "auth_requests" : "agent_scope_requests"
            canonicalId: root.model.canonical_id || ""
            humanDuration: root.model.human_duration || ""
            agentLabel: root.kind === "access" ? (root.model.identity_claim || "") : (root.display || root.model.canonical_id || "")
            selectable: root.kind === "scope"
            denyReasonRequired: root.kind === "scope"
            showEffectiveBinding: root.kind === "scope"
            onResolved: root.resolved()
            onRefetchRequested: root.refetchRequested()
        }
    }
}
