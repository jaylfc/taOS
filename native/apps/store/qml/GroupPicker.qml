// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Choose an app group, or make a new one. Used by the install step and by "Move to group". `exclude` hides the group
// the app is already in. `selected` is the chosen group's name ("default" is "My apps").
ColumnLayout {
    id: root
    property var engine
    property string selected: ""
    property string exclude: ""
    property bool compact: false
    signal picked(string group)
    spacing: 6
    readonly property var groups: engine && engine.revision >= 0 ? engine.appGroups().filter(g => g.name !== root.exclude) : []
    readonly property var work: engine && engine.revision >= 0 ? engine.groupWork : ({ state: "idle" })
    property bool naming: false
    Component.onCompleted: { engine.refreshGroups(); pickFirst() }
    // Called each time the picker is shown again (the dialog keeps its content between uses).
    function reset() {
        selected = ""
        naming = false
        engine.refreshGroups()
        pickFirst()
    }
    function pickFirst() {
        if (selected !== "" || groups.length === 0) return
        // the group made or used last, else the first (the default group)
        const last = engine.lastAppGroup()
        const hit = groups.filter(g => g.name === last)
        selected = hit.length > 0 ? hit[0].name : groups[0].name
        picked(selected)
    }
    onGroupsChanged: pickFirst()
    // a group that has just been made is the one to use
    // a group made here is chosen, and the name box closes
    Connections {
        target: root.engine
        function onGroupCreated(name) {
            root.selected = name
            root.naming = false
            nameField.text = ""
            root.picked(name)
        }
    }

    TaosText { Layout.topMargin: Look.space1; text: qsTr("App group"); font.pixelSize: Look.small; color: Look.textSecondary
               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
    Repeater {
        model: root.groups
        delegate: C.ItemDelegate {
            id: row
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: 44
            padding: 0
            hoverEnabled: true
            objectName: "group-" + modelData.name
            readonly property bool on: root.selected === modelData.name
            Accessible.role: Accessible.RadioButton
            Accessible.name: modelData.displayName + ", " + (modelData.apps === 1 ? qsTr("1 app") : qsTr("%1 apps").arg(modelData.apps))
            Accessible.checked: on
            onClicked: { root.selected = modelData.name; root.picked(modelData.name) }
            background: Rectangle {
                radius: Look.radiusControl
                color: row.on ? Look.tint(Look.accent, 0.12) : row.down ? Look.pressed : row.hovered ? Look.hover : Look.field
                border.width: 1
                border.color: row.visualFocus ? Look.focusRing : row.on ? Look.accent : Look.hairline
                Behavior on color { ColorAnimation { duration: Look.fast } }
            }
            contentItem: RowLayout {
                spacing: Look.space3
                Glyph { Layout.leftMargin: Look.space3; name: row.on ? "check-circle" : "circle-dashed"; size: 16
                        color: row.on ? Look.accent : Look.textTertiary }
                TaosText { Layout.fillWidth: true; text: row.modelData.displayName; elide: Text.ElideRight; font.weight: Font.DemiBold }
                TaosText { Layout.rightMargin: Look.space3; text: (row.modelData.apps === 1 ? qsTr("1 app") : qsTr("%1 apps").arg(row.modelData.apps)); color: Look.textSecondary; font.pixelSize: Look.small }
            }
        }
    }
    TaosText {
        visible: root.groups.length === 0 && !root.naming
        Layout.fillWidth: true; wrapMode: Text.Wrap
        text: qsTr("There is no other group yet. Make one to keep these apps together, away from your other apps.")
        color: Look.textSecondary; font.pixelSize: Look.small
    }

    // a new group
    TaosButton {
        visible: !root.naming
        text: qsTr("New group…"); glyph: "plus"; variant: "ghost"; compact: true
        objectName: "newGroupButton"
        accessibleName: qsTr("Make a new app group")
        onClicked: { root.naming = true; nameField.text = ""; nameField.forceActiveFocus() }
    }
    ColumnLayout {
        visible: root.naming
        Layout.fillWidth: true
        spacing: 6
        TaosField {
            id: nameField
            objectName: "groupNameField"
            Layout.fillWidth: true
            placeholderText: qsTr("A name, for example Dev apps")
            accessibleName: qsTr("Name of the new app group")
            enabled: root.work.state !== "creating"
            onAccepted: if (createButton.enabled) createButton.clicked()
        }
        TaosText {
            Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
            visible: root.work.state === "creating"
            text: root.work.text ? root.work.text : ""
            Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text
        }
        TaosText {
            Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.danger
            visible: root.work.state === "failed"
            text: root.work.text ? root.work.text : ""
            Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text
        }
        RowLayout {
            spacing: 8
            TaosButton {
                id: createButton
                objectName: "createGroupButton"
                text: root.work.state === "creating" ? qsTr("Creating…") : qsTr("Create group")
                variant: "primary"; compact: true
                enabled: root.work.state !== "creating" && root.engine.groupSlug(nameField.text) !== ""
                accessibleName: qsTr("Create the app group")
                onClicked: root.engine.createAppGroup(nameField.text)
            }
            TaosButton { text: qsTr("Cancel"); variant: "ghost"; compact: true; enabled: root.work.state !== "creating"; onClicked: root.naming = false }
        }
    }
}
