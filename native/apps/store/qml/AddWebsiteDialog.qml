// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// "Add any website": the address, an optional name, and whether to fetch the site's icon. The address is checked
// by the web apps engine as you type (https only, no file:, javascript: or data:).
C.Popup {
    id: root
    property var engine
    property bool allowHttp: false
    objectName: "addWebsiteDialog"
    modal: true
    focus: true
    padding: 0
    closePolicy: C.Popup.CloseOnEscape | C.Popup.CloseOnPressOutside
    anchors.centerIn: C.Overlay.overlay
    width: Math.min(440, (parent ? parent.width : 440) - 32)
    readonly property string problem: urlField.text.trim().length === 0 ? "" : engine.webapps.checkUrl(urlField.text, allowHttp)
    readonly property bool valid: urlField.text.trim().length > 0 && problem === ""
    function submit() {
        if (!valid) return
        engine.addWebsite(urlField.text.trim(), nameField.text.trim(), iconCheck.checked)
        close()
    }
    onOpened: { urlField.text = ""; nameField.text = ""; iconCheck.checked = true; urlField.forceActiveFocus() }

    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Look.base } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Look.fast } }
    C.Overlay.modal: Rectangle { color: Look.tint(Look.ground, 0.55) }

    background: Rectangle {
        radius: Look.radiusCard
        color: Look.raised
        border.width: 1
        border.color: Look.hairline
    }
    contentItem: ColumnLayout {
        spacing: 12
        Accessible.role: Accessible.Dialog
        Accessible.name: qsTr("Add any website")
        TaosText {
            Layout.topMargin: 20; Layout.leftMargin: 20; Layout.rightMargin: 20
            text: qsTr("Add any website")
            font.pixelSize: Look.title; font.weight: Font.DemiBold
            Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text
        }
        TaosText {
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.fillWidth: true
            text: qsTr("It opens in its own window with its own storage and sign-in, separate from every other web app.")
            wrapMode: Text.Wrap; color: Look.textSecondary; font.pixelSize: Look.small
        }
        ColumnLayout {
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.fillWidth: true
            spacing: 4
            TaosText { text: qsTr("Address"); font.pixelSize: Look.small; color: Look.textSecondary }
            TaosField {
                id: urlField
                objectName: "urlField"
                Layout.fillWidth: true
                placeholderText: qsTr("youtube.com or https://example.com")
                accessibleName: qsTr("Website address")
                inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
                KeyNavigation.tab: nameField
                onAccepted: root.submit()
            }
            TaosText {
                objectName: "urlProblem"
                Layout.fillWidth: true
                visible: root.problem !== ""
                text: root.problem
                wrapMode: Text.Wrap
                color: Look.danger; font.pixelSize: Look.small
                Accessible.ignored: false; Accessible.role: Accessible.AlertMessage; Accessible.name: text
            }
        }
        ColumnLayout {
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.fillWidth: true
            spacing: 4
            TaosText { text: qsTr("Name (optional)"); font.pixelSize: Look.small; color: Look.textSecondary }
            TaosField {
                id: nameField
                objectName: "nameField"
                Layout.fillWidth: true
                placeholderText: qsTr("Defaults to the site's own title")
                accessibleName: qsTr("Name of the web app")
                KeyNavigation.tab: iconCheck
                onAccepted: root.submit()
            }
        }
        TaosCheck {
            id: iconCheck
            objectName: "iconCheck"
            Layout.leftMargin: 20; Layout.rightMargin: 20
            text: qsTr("Use the site's own icon when it can be fetched")
            checked: true
            KeyNavigation.tab: addButton
        }
        RowLayout {
            Layout.leftMargin: 20; Layout.rightMargin: 20; Layout.bottomMargin: 20; Layout.topMargin: 4
            spacing: 8
            Item { Layout.fillWidth: true }
            TaosButton { text: qsTr("Cancel"); variant: "ghost"; onClicked: root.close() }
            TaosButton {
                id: addButton
                objectName: "addButton"
                text: qsTr("Add")
                variant: "primary"
                enabled: root.valid
                accessibleName: qsTr("Add the website as a web app")
                onClicked: root.submit()
            }
        }
    }
}
