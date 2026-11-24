import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0

// Ctrl+K: jump to any conversation by typing part of its name.
Item {
    id: root
    required property var engine
    property bool open: false
    signal chosen(string channelId)
    visible: open
    anchors.fill: parent
    z: 50
    objectName: "switcher"

    function show() { open = true; field.text = ""; refilter(); field.forceActiveFocus() }
    function hide() { open = false }
    property var results: []
    function refilter() { results = engine.searchChannels(field.text); list.currentIndex = results.length > 0 ? 0 : -1 }

    Rectangle { anchors.fill: parent; color: Look.ground; opacity: 0.6
        MouseArea { anchors.fill: parent; onClicked: root.hide() } }

    Rectangle {
        width: Math.min(460, parent.width - 32); height: Math.min(380, 56 + list.count * 36 + 12)
        anchors.horizontalCenter: parent.horizontalCenter; y: 60
        radius: 12; color: Look.raised; border.width: 1; border.color: Look.hairline
        Accessible.role: Accessible.Dialog
        Accessible.name: qsTr("Switch conversation")
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 8; spacing: 6
            C.TextField {
                id: field
                objectName: "switcherField"
                Layout.fillWidth: true; implicitHeight: 40
                placeholderText: qsTr("Go to a conversation")
                placeholderTextColor: Look.textTertiary
                color: Look.text; font.family: Look.family; font.pixelSize: Look.bodyLarge
                leftPadding: 10
                Accessible.name: qsTr("Conversation name")
                background: Rectangle { radius: 8; color: Look.field; border.width: 2; border.color: Look.focusRing }
                onTextChanged: root.refilter()
                Keys.onDownPressed: list.currentIndex = Math.min(list.count - 1, list.currentIndex + 1)
                Keys.onUpPressed: list.currentIndex = Math.max(0, list.currentIndex - 1)
                Keys.onReturnPressed: root.pick()
                Keys.onEnterPressed: root.pick()
                Keys.onEscapePressed: root.hide()
            }
            ListView {
                id: list
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; model: root.results; currentIndex: 0
                Accessible.role: Accessible.List; Accessible.name: qsTr("Matching conversations")
                delegate: Rectangle {
                    required property var modelData
                    required property int index
                    width: list.width; height: 36; radius: 7
                    color: ListView.isCurrentItem ? Look.selection : "transparent"
                    Accessible.role: Accessible.ListItem; Accessible.name: modelData.name
                    Row {
                        anchors.verticalCenter: parent.verticalCenter; x: 10; spacing: 8
                        TaosText { text: modelData.name; font.weight: modelData.unread > 0 ? Font.DemiBold : Font.Normal }
                        TaosText { text: modelData.section; color: Look.textTertiary; font.pixelSize: Look.small }
                    }
                    MouseArea { anchors.fill: parent; onClicked: { list.currentIndex = index; root.pick() } }
                }
            }
        }
    }
    function pick() {
        if (list.currentIndex < 0 || list.currentIndex >= results.length) return
        const id = results[list.currentIndex].channelId
        hide()
        chosen(id)
    }
}
