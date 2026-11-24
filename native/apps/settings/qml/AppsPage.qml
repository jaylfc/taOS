// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Store 1.0
import Taos.Style 1.0

// Settings > Apps: every app on this device. Web apps (open, rename, clone with the sign-in, clear data, remove),
// container apps (listed from the controller; removed in the Store) and taOS's own apps.
PageBase {
    id: page
    sectionId: "apps"

    AppsOverview {
        id: ov
        objectName: "appsOverview"
        active: page.visible
    }
    readonly property var web: ov.revision >= 0 ? ov.webInstances : []
    readonly property var containers: ov.revision >= 0 ? ov.containers : []
    readonly property var taosApps: ov.revision >= 0 ? ov.native : []
    property string toast: ""
    Connections {
        target: ov.webapps()
        function onFinished(op, ok, id, text, code) { page.toast = text; toastTimer.restart(); Settings.announce(page, text) }
    }
    Timer { id: toastTimer; interval: 4500; onTriggered: page.toast = "" }

    Txt {
        width: parent.width
        visible: !ov.browserAvailable
        objectName: "noBrowser"
        text: ov.browserText !== "" ? ov.browserText : qsTr("No browser engine installed. Web apps need Chromium.")
        color: TaosStyle.warning
        font.pixelSize: 12
        Accessible.role: Accessible.AlertMessage
    }
    Txt {
        width: parent.width
        visible: page.toast !== ""
        objectName: "appsToast"
        text: page.toast
        color: TaosStyle.textSecondary
        font.pixelSize: 12
        Accessible.role: Accessible.AlertMessage
    }

    Group {
        heading: qsTr("Web apps")
        footnote: qsTr("Each web app keeps its own storage and sign-in. Clear data signs it out; Remove deletes it.")
        Repeater {
            model: page.web
            delegate: WebAppRow { required property var modelData; overview: ov; inst: modelData }
        }
        Item {
            visible: page.web.length === 0
            width: parent.width; height: 56
            Txt { anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter } text: qsTr("No web apps yet.")
                  color: TaosStyle.textSecondary }
            Btn { objectName: "openStoreWeb"; anchors { right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
                  text: qsTr("Add in the Store"); onClicked: ov.openStore("webapps") }
        }
    }

    Group {
        heading: qsTr("Container apps")
        footnote: qsTr("Installed on the controller. Open the Store to update or remove them.")
        Repeater {
            model: page.containers
            delegate: Item {
                id: crow
                required property var modelData
                width: parent.width; height: 56
                objectName: "container-" + modelData.id
                Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 } height: 1; color: TaosStyle.hairline }
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name + ", " + modelData.subtitle
                RowLayout {
                    anchors { fill: parent; leftMargin: 16; rightMargin: 16 }
                    spacing: 12
                    Rectangle { width: 32; height: 32; radius: 8; color: TaosStyle.track
                                Txt { anchors.centerIn: parent; text: crow.modelData.letter; color: TaosStyle.textSecondary
                                      font.weight: Font.DemiBold; wrapMode: Text.NoWrap } }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        Txt { Layout.fillWidth: true; text: crow.modelData.name; font.weight: Font.DemiBold; elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                        Txt { Layout.fillWidth: true; text: crow.modelData.subtitle; font.pixelSize: 12; color: TaosStyle.textSecondary
                              elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                    }
                    Btn { text: qsTr("Manage in Store"); Accessible.name: qsTr("Manage %1 in the Store").arg(crow.modelData.name)
                          onClicked: ov.openInStore(crow.modelData.id) }
                }
            }
        }
        Item {
            visible: page.containers.length === 0
            width: parent.width; height: 56
            Txt { anchors { left: parent.left; leftMargin: 16; right: parent.right; rightMargin: 16; verticalCenter: parent.verticalCenter }
                  objectName: "containersEmpty"
                  text: ov.containersError !== "" ? ov.containersError : ov.containersLoaded ? qsTr("No container apps are installed.") : qsTr("Loading from the controller.")
                  color: ov.containersError !== "" ? TaosStyle.danger : TaosStyle.textSecondary
                  wrapMode: Text.NoWrap; elide: Text.ElideRight }
        }
    }

    Group {
        heading: qsTr("taOS apps")
        Repeater {
            model: page.taosApps
            delegate: Item {
                id: nrow
                required property var modelData
                width: parent.width; height: 56
                objectName: "native-" + modelData.id
                Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 } height: 1; color: TaosStyle.hairline }
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name + ", " + modelData.comment
                RowLayout {
                    anchors { fill: parent; leftMargin: 16; rightMargin: 16 }
                    spacing: 12
                    Rectangle { width: 32; height: 32; radius: 8; color: TaosStyle.track
                                Txt { anchors.centerIn: parent; text: nrow.modelData.letter; color: TaosStyle.textSecondary
                                      font.weight: Font.DemiBold; wrapMode: Text.NoWrap } }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        Txt { Layout.fillWidth: true; text: nrow.modelData.name; font.weight: Font.DemiBold; elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                        Txt { Layout.fillWidth: true; text: nrow.modelData.comment; font.pixelSize: 12; color: TaosStyle.textSecondary
                              elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                    }
                    Txt { text: qsTr("Part of taOS"); font.pixelSize: 12; color: TaosStyle.textTertiary; wrapMode: Text.NoWrap }
                    Btn { text: qsTr("Open"); enabled: nrow.modelData.canOpen; Accessible.name: qsTr("Open %1").arg(nrow.modelData.name)
                          onClicked: ov.openNative(nrow.modelData.id) }
                }
            }
        }
        Item {
            visible: page.taosApps.length === 0
            width: parent.width; height: 56
            Txt { anchors { left: parent.left; leftMargin: 16; verticalCenter: parent.verticalCenter } text: qsTr("No taOS apps were found on this device.")
                  color: TaosStyle.textSecondary }
        }
    }
}
