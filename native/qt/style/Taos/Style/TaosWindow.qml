import QtQuick
import QtQuick.Controls
import Taos.Style 1.0

// The app window of a native taOS app: themed background, the taOS title bar (close, minimise, full size;
// `titleBar: false` for an app that draws its own), and a banner that says so when the controller cannot be
// reached or the data may be stale. `connectionText` is empty when all is well. The compositor draws no title
// bar for these windows: the 1 px border stays, this header is the window's chrome.
ApplicationWindow {
    id: root
    property string connectionText: ""
    property bool titleBar: true
    color: TaosStyle.ground
    visible: true
    flags: titleBar ? (Qt.Window | Qt.FramelessWindowHint) : Qt.Window
    header: titleBar ? bar : null

    TaosTitleBar {
        id: bar
        window: root
        visible: root.titleBar
    }

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
