// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// One person on the mesh: where they were last, their trail, their last messages, who is behind the radio, and for a
// contact the two things the owner must handle: telling them positions are stored, and linking their taOS account.
C.ScrollView {
    id: root
    property var nav
    property string node: ""
    readonly property var tak: Survival.tak
    readonly property var p: { tak.people; return tak.person(node) }
    readonly property var contact: { tak.contacts; for (const c of tak.contacts) if (c.id === (p.contactId || "")) return c; return null }
    readonly property var trail: { tak.people; return tak.trail(node) }
    readonly property var recent: { tak.threads; const out = []; for (const m of tak.messages(node)) out.push(m); return out.slice(-3).reverse() }
    contentWidth: availableWidth
    clip: true
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space3
        RowLayout {
            Layout.topMargin: Look.space3
            spacing: Look.space3
            Rectangle {
                width: 44; height: 44; radius: 22
                color: Look.tint(Look.accent, 0.14); border.width: 1; border.color: Look.tint(Look.accent, 0.30)
                Glyph { anchors.centerIn: parent; name: root.p.kind === "agent" ? "bot" : root.p.kind === "person" ? "user" : root.p.kind === "contact" ? "users" : "radio"; size: 20; stroke: 1.8; color: Look.accent }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                TaosText { text: root.p.name || root.node; font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText { text: root.node; font.pixelSize: Look.small; color: Look.textSecondary; font.family: "monospace" }
            }
        }
        GlassPanel {
            Layout.fillWidth: true
            implicitHeight: posCol.implicitHeight + Look.space3 * 2
            ColumnLayout {
                id: posCol
                anchors { fill: parent; margins: Look.space3 }
                spacing: Look.space2
                TakLabel { text: qsTr("Last known position") }
                TaosText { text: root.p.hasPosition ? root.p.coords : qsTr("No position heard yet"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
                TaosText { visible: root.p.hasPosition; text: qsTr("Seen %1").arg(root.p.positionAgo || ""); font.pixelSize: Look.body; color: Look.textSecondary }
                TaosText { text: qsTr("Last heard %1").arg(root.p.heardAgo || ""); font.pixelSize: Look.small; color: Look.textSecondary }
                PillButton { visible: root.p.hasPosition; compact: true; text: qsTr("Show on the map"); glyph: "map-pin"; onClicked: root.nav.stage.centerOn(root.p.lon, root.p.lat) }
            }
        }
        TakLabel { text: qsTr("Trail") }
        TaosText { visible: root.trail.length === 0; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                   text: qsTr("No earlier positions stored for this radio.") }
        Repeater {
            model: root.trail.slice(-6).reverse()
            delegate: RowLayout {
                required property var modelData
                Layout.fillWidth: true
                spacing: Look.space3
                Rectangle { width: 8; height: 8; radius: 4; color: Look.accent; Layout.alignment: Qt.AlignVCenter }
                TaosText { text: modelData.coords; font.pixelSize: Look.body; Layout.fillWidth: true; elide: Text.ElideRight; font.features: { "tnum": 1 } }
                TaosText { text: modelData.ago; font.pixelSize: Look.small; color: Look.textSecondary }
            }
        }
        TakLabel { text: qsTr("Last messages") }
        TaosText { visible: root.recent.length === 0; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                   text: qsTr("No direct messages with this person.") }
        Repeater {
            model: root.recent
            delegate: ColumnLayout {
                required property var modelData
                Layout.fillWidth: true; spacing: 0
                TaosText { text: modelData.who + "  " + modelData.ago; font.pixelSize: Look.small; color: Look.textSecondary }
                TaosText { text: modelData.text; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body }
            }
        }
        PillButton { compact: true; text: qsTr("Open the conversation"); glyph: "message"; onClicked: root.nav.open("thread", root.node) }

        // ---- the contact's own matters
        ColumnLayout {
            visible: root.contact !== null
            Layout.fillWidth: true
            spacing: Look.space2
            TakLabel { text: qsTr("Contact") }
            TaosText { visible: root.contact !== null && root.contact.notes !== ""; text: root.contact ? root.contact.notes : ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
            GlassPanel {
                Layout.fillWidth: true
                implicitHeight: toldCol.implicitHeight + Look.space3 * 2
                ColumnLayout {
                    id: toldCol
                    anchors { fill: parent; margins: Look.space3 }
                    spacing: Look.space2
                    RowLayout {
                        spacing: Look.space2
                        TaosText { text: qsTr("Told about stored positions"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Chip { objectName: "toldChip"; text: root.contact && root.contact.told ? (root.contact.toldDate !== "" ? qsTr("Told %1").arg(root.contact.toldDate) : qsTr("Told")) : qsTr("Not told yet")
                               tone: root.contact && root.contact.told ? "success" : "warning"; glyph: root.contact && root.contact.told ? "check" : "alert" }
                    }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: qsTr("Positions their radio broadcasts are kept. Tell them, then confirm here.") }
                    PillButton { objectName: "toldConfirm"; compact: true; variant: root.contact && root.contact.told ? "outline" : "primary"
                                 text: root.contact && root.contact.told ? qsTr("Mark as not told") : qsTr("I told them")
                                 onClicked: root.contact.told ? root.tak.clearTold(root.contact.id) : root.tak.confirmTold(root.contact.id) }
                }
            }
            GlassPanel {
                Layout.fillWidth: true
                implicitHeight: linkCol.implicitHeight + Look.space3 * 2
                ColumnLayout {
                    id: linkCol
                    anchors { fill: parent; margins: Look.space3 }
                    spacing: Look.space2
                    TaosText { text: qsTr("Link to taOS account"); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                               text: root.contact && root.contact.linkedAccount !== "" ? qsTr("Linked to %1. You both keep both sides of the conversations you share.").arg(root.contact.linkedAccount)
                                                                                         : qsTr("Once linked, you both keep both sides of the conversations you share, and they get their radio history.") }
                    RowLayout {
                        visible: !(root.contact && root.contact.linkedAccount !== "")
                        Layout.fillWidth: true; spacing: Look.space2
                        TaosField { id: acct; Layout.fillWidth: true; placeholderText: qsTr("taOS account id"); accessibleName: qsTr("taOS account id") }
                        PillButton { objectName: "linkApply"; compact: true; variant: "primary"; text: qsTr("Link"); enabled: acct.text.trim() !== ""; onClicked: root.tak.link(root.contact.id, acct.text) }
                    }
                }
            }
        }
        TakLabel { text: qsTr("Who is behind this radio") }
        TakAssign { Layout.fillWidth: true; node: root.node; current: root.p.identity || "" }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
