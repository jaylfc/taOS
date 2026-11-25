// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// "Move to group": pick the group (or make one) and move the app with its data. The app must be closed; the old data
// is kept as a backup until you delete it.
C.Popup {
    id: root
    property var engine
    property string appId: ""
    property string appName: ""
    property string fromGroup: ""
    property string target: ""
    objectName: "moveToGroupDialog"
    modal: true
    focus: true
    padding: 0
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    anchors.centerIn: C.Overlay.overlay
    width: Math.min(460, (parent ? parent.width : 460) - 32)
    height: Math.min(contentItem.implicitHeight, (parent ? parent.height : 640) - 32)
    function openFor(id, name, group) { appId = id; appName = name; fromGroup = group; target = ""; open() }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Look.base } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Look.fast } }
    C.Overlay.modal: Rectangle { color: Look.tint(Look.ground, 0.55) }
    background: Rectangle { radius: Look.radiusCard; color: Look.raised; border.width: 1; border.color: Look.hairline }
    contentItem: C.ScrollView {
        id: sv
        clip: true
        contentWidth: availableWidth
        implicitHeight: col.implicitHeight + 40
        ColumnLayout {
            id: col
            x: 20; y: 20
            width: sv.availableWidth - 40
            spacing: 12
            Accessible.role: Accessible.Dialog
            Accessible.name: qsTr("Move %1 to a group").arg(root.appName)
            TaosText { text: qsTr("Move %1 to a group").arg(root.appName); Layout.fillWidth: true; wrapMode: Text.Wrap
                       font.pixelSize: Look.title; font.weight: Font.DemiBold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText {
                Layout.fillWidth: true; wrapMode: Text.Wrap; color: Look.textSecondary; font.pixelSize: Look.small
                text: qsTr("%1 and its data (sign-ins, settings, profile) move together. Close the app first. The old data is kept as a backup until you delete it.").arg(root.appName)
            }
            GroupPicker {
                id: picker
                Layout.fillWidth: true
                engine: root.engine
                exclude: root.fromGroup
                onPicked: (g) => root.target = g
            }
            RowLayout {
                Layout.topMargin: 4
                spacing: 8
                Item { Layout.fillWidth: true }
                TaosButton { text: qsTr("Cancel"); variant: "ghost"; onClicked: root.close() }
                TaosButton {
                    objectName: "moveButton"
                    text: qsTr("Move")
                    variant: "primary"
                    enabled: root.target !== "" && root.target !== root.fromGroup
                    accessibleName: qsTr("Move %1 to the chosen group").arg(root.appName)
                    onClicked: { root.engine.moveContainerApp(root.appId, root.target); root.close() }
                }
            }
        }
    }
}
