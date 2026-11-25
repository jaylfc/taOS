// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// People: you, everyone heard on the air with who is behind each radio and when they were last seen, and the
// notes kept on this device (the contacts that were here before the mesh engine).
C.ScrollView {
    id: root
    property var nav
    readonly property var tak: Survival.tak
    readonly property var map: Survival.map
    contentWidth: availableWidth
    clip: true
    function kindGlyph(k) { return k === "agent" ? "bot" : k === "person" ? "user" : k === "contact" ? "users" : "radio" }
    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space1
        TakLabel { text: qsTr("You"); Layout.topMargin: Look.space3; Layout.leftMargin: Look.space3 }
        ListRow {
            Layout.fillWidth: true
            objectName: "meRow"
            glyph: "user"
            tone: root.map.hasMe ? "accent" : "neutral"
            clickable: root.map.hasMe
            title: root.map.hasMe ? root.map.formatCoords(root.map.meLon, root.map.meLat) : qsTr("Position not set")
            subtitle: root.map.meSource === "device" ? qsTr("From this device") : root.map.meSource === "manual" ? qsTr("Set by you on the map") : qsTr("Set it on the map, or turn on location for this device")
            onClicked: root.nav.stage.centerOn(root.map.meLon, root.map.meLat)
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; spacing: Look.space2
            PillButton { Layout.fillWidth: true; compact: true; text: qsTr("Set on the map"); glyph: "map-pin"; onClicked: { root.nav.stage.pickingMe = true; root.nav.stage.forceActiveFocus() } }
            PillButton { visible: root.map.meSource === "manual"; compact: true; text: qsTr("Clear"); onClicked: root.map.clearMe() }
        }
        TakLabel { text: qsTr("On the mesh"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3 }
        NetworkFilter { Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; Layout.bottomMargin: Look.space2 }
        Repeater {
            model: root.tak.people
            delegate: ListRow {
                required property var modelData
                Layout.fillWidth: true
                objectName: "person-" + modelData.node
                glyph: root.kindGlyph(modelData.kind)
                tone: modelData.kind === "none" ? "neutral" : "accent"
                title: modelData.name + (modelData.isSelf ? qsTr(" (your radio)") : "")
                subtitle: modelData.hasPosition ? qsTr("Seen %1").arg(modelData.positionAgo) : qsTr("Last heard %1, no position").arg(modelData.heardAgo)
                detail: (root.tak.networks.length > 1 ? modelData.networkName + "  -  " : "") + (modelData.kind === "none" ? qsTr("Nobody assigned to this radio yet") : modelData.kind === "agent" ? qsTr("An agent") : modelData.kind === "person" ? qsTr("A taOS person")
                        : modelData.linkedAccount !== "" ? qsTr("Contact, linked to a taOS account") : qsTr("Contact")) + (modelData.hasPosition ? "  -  " + modelData.coords : "")
                onClicked: root.nav.open("person", modelData.node)
                trailing: Component { Glyph { name: "chevron-right"; size: 16; stroke: 2; color: Look.textTertiary } }
            }
        }
        EmptyState {
            visible: root.tak.people.length === 0
            Layout.fillWidth: true; Layout.topMargin: Look.space4
            glyph: "radio"
            title: root.tak.available ? qsTr("Nobody heard yet") : qsTr("No mesh engine")
            detail: root.tak.available ? qsTr("When a radio hears someone, they show up here with their last position.") : qsTr("People heard on the air appear here once the mesh engine runs.")
        }
        TakLabel { text: qsTr("Notes on this device"); Layout.topMargin: Look.space4; Layout.leftMargin: Look.space3 }
        Repeater {
            model: root.map.contacts
            delegate: ListRow {
                required property var modelData
                Layout.fillWidth: true
                glyph: "users"; clickable: false
                title: modelData.callsign
                subtitle: modelData.nodeId !== "" ? qsTr("Node %1").arg(modelData.nodeId) : qsTr("No node id")
                detail: qsTr("Kept on this device only. Not part of the mesh engine.")
                trailing: Component {
                    TaosButton { glyph: "trash"; compact: true; variant: "ghost"; accessibleName: qsTr("Remove %1").arg(modelData.callsign); onClicked: root.map.removeContact(modelData.id) }
                }
            }
        }
        TaosText {
            visible: root.map.contacts.length === 0
            Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; wrapMode: Text.Wrap
            font.pixelSize: Look.small; color: Look.textSecondary
            text: qsTr("Jot down a callsign and node id. For radios the engine can hear, assign them above instead.")
        }
        RowLayout {
            Layout.fillWidth: true; Layout.leftMargin: Look.space3; Layout.rightMargin: Look.space3; Layout.topMargin: Look.space2; spacing: Look.space2
            TaosField { id: callsign; Layout.fillWidth: true; placeholderText: qsTr("Callsign or name"); accessibleName: qsTr("Callsign or name") }
            TaosField { id: nodeId; Layout.preferredWidth: 96; placeholderText: qsTr("Node id"); accessibleName: qsTr("Node id") }
            TaosButton { glyph: "plus"; variant: "primary"; enabled: callsign.text.trim() !== ""; accessibleName: qsTr("Add note")
                         onClicked: { root.map.addContact(callsign.text, nodeId.text, ""); callsign.text = ""; nodeId.text = "" } }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
