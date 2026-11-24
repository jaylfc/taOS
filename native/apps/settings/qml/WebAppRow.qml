import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

// One web app instance in Settings > Apps: name, site, storage used, last used; Open, and Rename, Clone, Clear data
// and Remove behind the row (a second click confirms what deletes). `overview` is the AppsOverview.
Item {
    id: row
    property var overview
    property var inst: ({})
    property bool open: false
    property string confirm: ""
    property bool renaming: false
    readonly property bool busy: overview && overview.revision >= 0 ? overview.webapps().isBusy(inst.id) : false
    width: parent ? parent.width : 400
    implicitHeight: head.height + (open ? strip.implicitHeight + 14 : 0)
    height: implicitHeight
    clip: true
    objectName: "webapp-" + inst.id

    Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 } height: 1; color: TaosStyle.hairline }
    Rectangle {
        id: head
        width: parent.width
        height: 58
        color: hover.hovered || fs.activeFocus ? TaosStyle.selection : "transparent"
        FocusScope {
            id: fs
            anchors.fill: parent
            activeFocusOnTab: true
            Keys.onReturnPressed: row.open = !row.open
            Keys.onSpacePressed: row.open = !row.open
            Accessible.role: Accessible.ListItem
            Accessible.name: inst.name + ", " + inst.host + ", " + inst.sizeText + ", " + inst.lastUsedText
            Accessible.onPressAction: row.open = !row.open
        }
        HoverHandler { id: hover }
        TapHandler { onTapped: { fs.forceActiveFocus(); row.open = !row.open } }
        RowLayout {
            anchors { fill: parent; leftMargin: 16; rightMargin: 16 }
            spacing: 12
            Rectangle {
                width: 32; height: 32; radius: 8; color: TaosStyle.track
                Image {
                    anchors.fill: parent; anchors.margins: 4
                    source: inst.iconUrl ? inst.iconUrl : ""
                    sourceSize: Qt.size(64, 64); fillMode: Image.PreserveAspectFit
                    visible: status === Image.Ready; asynchronous: true
                }
                Txt { anchors.centerIn: parent; text: inst.letter ? inst.letter : "?"; color: TaosStyle.textSecondary
                      font.weight: Font.DemiBold; wrapMode: Text.NoWrap; visible: !inst.iconUrl }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 1
                Txt { Layout.fillWidth: true; text: inst.name; font.weight: Font.DemiBold; elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
                Txt { Layout.fillWidth: true; text: inst.host + "  ·  " + inst.sizeText + "  ·  " + inst.lastUsedText
                      font.pixelSize: 12; color: TaosStyle.textSecondary; elide: Text.ElideRight; wrapMode: Text.NoWrap; Accessible.ignored: true }
            }
            Txt { visible: inst.running === true; text: qsTr("Open now"); font.pixelSize: 12; color: TaosStyle.ok; wrapMode: Text.NoWrap }
            Txt { visible: row.busy; text: row.overview ? row.overview.webapps().busyText(inst.id) : ""; font.pixelSize: 12
                  color: TaosStyle.textSecondary; wrapMode: Text.NoWrap }
            Btn { objectName: "openButton"; text: qsTr("Open"); kind: "accent"; enabled: !row.busy
                  Accessible.name: qsTr("Open %1").arg(inst.name); onClicked: row.overview.webapps().launch(inst.id) }
        }
    }
    ColumnLayout {
        id: strip
        visible: row.open
        anchors { left: parent.left; right: parent.right; top: head.bottom; leftMargin: 60; rightMargin: 16; topMargin: 2 }
        spacing: 8
        Txt { Layout.fillWidth: true; text: qsTr("Window id %1. Its own storage and sign-in; nothing is shared with other web apps.").arg(inst.app_id)
              font.pixelSize: 12; color: TaosStyle.textSecondary }
        RowLayout {
            visible: row.renaming
            spacing: 8
            Rectangle {
                Layout.preferredWidth: 220; height: 30; radius: 7; color: TaosStyle.track
                border.width: nameInput.activeFocus ? 2 : 1
                border.color: nameInput.activeFocus ? TaosStyle.focusRing : TaosStyle.hairline
                TextInput {
                    id: nameInput
                    objectName: "renameInput"
                    anchors { fill: parent; leftMargin: 8; rightMargin: 8 }
                    verticalAlignment: TextInput.AlignVCenter
                    color: TaosStyle.text; font.family: "Inter"; font.pixelSize: TaosStyle.bodySize; selectByMouse: true
                    text: inst.name
                    Accessible.name: qsTr("New name"); Accessible.role: Accessible.EditableText
                    onVisibleChanged: if (visible) { forceActiveFocus(); selectAll() }
                    onAccepted: save.clicked()
                }
            }
            Btn { id: save; objectName: "renameSave"; text: qsTr("Save"); kind: "accent"
                  onClicked: { row.overview.webapps().rename(inst.id, nameInput.text); row.renaming = false } }
            Btn { text: qsTr("Cancel"); onClicked: row.renaming = false }
        }
        Flow {
            Layout.fillWidth: true; spacing: 8
            visible: row.confirm === "" && !row.renaming
            Btn { objectName: "renameButton"; text: qsTr("Rename"); enabled: !row.busy; onClicked: row.renaming = true }
            Btn { objectName: "cloneButton"; text: qsTr("Clone"); enabled: !row.busy
                  Accessible.name: qsTr("Clone %1, copying its sign-in").arg(inst.name); onClicked: row.overview.webapps().clone(inst.id) }
            Btn { objectName: "clearButton"; text: qsTr("Clear data"); enabled: !row.busy; onClicked: row.confirm = "clear" }
            Btn { objectName: "removeButton"; text: qsTr("Remove"); kind: "danger"; enabled: !row.busy; onClicked: row.confirm = "remove" }
        }
        RowLayout {
            visible: row.confirm !== ""
            spacing: 8
            Txt { Layout.fillWidth: true; font.pixelSize: 12
                  text: row.confirm === "clear" ? qsTr("Sign out and delete everything this web app stored? Its window must be closed.")
                                                : qsTr("Remove this web app and delete its sign-in and data? Its window must be closed.") }
            Btn { objectName: "confirmButton"; text: row.confirm === "clear" ? qsTr("Clear data") : qsTr("Remove"); kind: "danger"
                  onClicked: { if (row.confirm === "clear") row.overview.webapps().clearData(inst.id); else row.overview.webapps().remove(inst.id); row.confirm = "" } }
            Btn { text: qsTr("Keep"); onClicked: row.confirm = "" }
        }
    }
}
