// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The threads: one per channel and one per person, newest first, with unread counts.
ColumnLayout {
    id: root
    property var nav
    readonly property var tak: Survival.tak
    spacing: 0
    NetworkFilter { Layout.fillWidth: true; Layout.margins: Look.space3; Layout.bottomMargin: 0 }
    C.ScrollView {
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: Look.space3
            width: parent.width - Look.space3 * 2
            spacing: Look.space1
            Item { Layout.preferredHeight: Look.space2 }
            Repeater {
                model: root.tak.threads
                delegate: ListRow {
                    required property var modelData
                    Layout.fillWidth: true
                    objectName: "thread-" + modelData.id
                    glyph: modelData.kind === "channel" ? "users" : "user"
                    tone: modelData.unread > 0 ? "accent" : "neutral"
                    title: modelData.title
                    subtitle: modelData.subtitle
                    detail: modelData.ago + (modelData.network !== "" ? "  " + modelData.network : "")
                    onClicked: root.nav.open("thread", modelData.id)
                    trailing: Component {
                        Rectangle {
                            visible: modelData.unread > 0
                            implicitWidth: Math.max(20, ub.implicitWidth + 12); implicitHeight: 20; radius: 10; color: Look.accent
                            Accessible.role: Accessible.StaticText; Accessible.name: qsTr("%1 unread").arg(modelData.unread)
                            TaosText { id: ub; anchors.centerIn: parent; text: modelData.unread; font.pixelSize: Look.caption; font.weight: Font.DemiBold; color: Look.accentText }
                        }
                    }
                }
            }
            EmptyState {
                visible: root.tak.threads.length === 0
                Layout.fillWidth: true; Layout.topMargin: Look.space6
                glyph: "message"
                title: qsTr("No messages")
                detail: root.tak.available ? qsTr("Messages heard on your channels and sent to you appear here, with the sender's position and signal.")
                                           : qsTr("Messages appear here once the mesh engine runs. Everything it heard is kept, so nothing is lost while the app is closed.")
            }
        }
    }
}
