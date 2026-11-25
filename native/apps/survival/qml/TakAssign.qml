// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos 1.0
import Taos.Apps 1.0
import Taos.Survival 1.0

// Who a radio belongs to: you, an agent, a taOS person, or a named contact who is not on taOS. Used for each detected
// radio and for each person heard on the air. Everything the engine stores from that radio is attributed to this
// identity from then on.
ColumnLayout {
    id: root
    property string node: ""
    property string current: ""          // the identity it has now, or empty
    readonly property var tak: Survival.tak
    property string kind: "contact"
    property bool newContact: root.tak.contacts.length === 0
    spacing: Look.space2
    objectName: "assign-" + node
    Segmented {
        Layout.fillWidth: true
        accessibleName: qsTr("Who is this radio")
        model: [{id: "me", text: qsTr("Me")}, {id: "agent", text: qsTr("Agent")}, {id: "person", text: qsTr("Person")}, {id: "contact", text: qsTr("Contact")}]
        currentId: root.kind
        onActivated: (id) => root.kind = id
    }
    TaosText { visible: root.kind === "me"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
               text: qsTr("This is your own radio. Everything it hears is kept as yours.") }
    TaosField { id: agentField; visible: root.kind === "agent"; Layout.fillWidth: true; placeholderText: qsTr("Agent id"); accessibleName: qsTr("Agent id") }
    TaosField { id: personField; visible: root.kind === "person"; Layout.fillWidth: true; placeholderText: qsTr("taOS account id"); accessibleName: qsTr("taOS account id") }
    ColumnLayout {
        visible: root.kind === "contact"
        Layout.fillWidth: true
        spacing: Look.space2
        Flow {
            Layout.fillWidth: true
            spacing: Look.space2
            Repeater {
                model: root.tak.contacts
                delegate: PillButton {
                    required property var modelData
                    compact: true
                    text: modelData.name
                    variant: !root.newContact && root.pickedContact === modelData.id ? "primary" : "tint"
                    onClicked: { root.newContact = false; root.pickedContact = modelData.id }
                }
            }
            PillButton { compact: true; text: qsTr("New contact"); glyph: "plus"; variant: root.newContact ? "primary" : "outline"; onClicked: root.newContact = true }
        }
        TaosField { id: contactName; visible: root.newContact; Layout.fillWidth: true; placeholderText: qsTr("Name"); accessibleName: qsTr("Contact name") }
        TaosField { id: contactNotes; visible: root.newContact; Layout.fillWidth: true; placeholderText: qsTr("Notes (optional)"); accessibleName: qsTr("Contact notes") }
    }
    property string pickedContact: ""
    RowLayout {
        Layout.fillWidth: true
        spacing: Look.space2
        PillButton {
            objectName: "assignApply"
            Layout.fillWidth: true
            variant: "primary"
            compact: true
            text: qsTr("Assign radio")
            enabled: root.kind === "me" || (root.kind === "agent" && agentField.text.trim() !== "") || (root.kind === "person" && personField.text.trim() !== "")
                     || (root.kind === "contact" && (root.newContact ? contactName.text.trim() !== "" : root.pickedContact !== ""))
            onClicked: {
                if (root.kind === "me") root.tak.assign(root.node, root.tak.identityOf("person", Taos.session.userName !== "" ? Taos.session.userName : "me"), qsTr("Me"))
                else if (root.kind === "agent") root.tak.assign(root.node, root.tak.identityOf("agent", agentField.text), agentField.text.trim())
                else if (root.kind === "person") root.tak.assign(root.node, root.tak.identityOf("person", personField.text), personField.text.trim())
                else if (root.newContact) root.tak.createContact(contactName.text, contactNotes.text, root.node)
                else root.tak.assign(root.node, root.tak.identityOf("contact", root.pickedContact))
            }
        }
        PillButton { visible: root.current !== ""; compact: true; text: qsTr("Unassign"); variant: "outline"; onClicked: root.tak.assign(root.node, "") }
    }
    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textTertiary
               text: qsTr("Messages and positions this radio sends are stored under this name, so you and your agents know who sent what.") }
}
