import QtQuick
import QtQuick.Controls
import Taos.Style 1.0

// The minimal app window: themed background, and a banner that says so when
// the controller cannot be reached or the data may be stale. `connectionText`
// is empty when all is well.
ApplicationWindow {
    id: root
    property string connectionText: ""
    color: TaosStyle.ground
    visible: true

    Rectangle {
        z: 10
        width: parent.width
        height: visible ? 32 : 0
        visible: root.connectionText.length > 0
        color: TaosStyle.warning
        Text {
            anchors.centerIn: parent
            text: root.connectionText
            color: TaosStyle.ground
            font.pixelSize: TaosStyle.bodySize
        }
    }
}
