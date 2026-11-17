import QtQuick
import Poc

Window {
    id: bar
    width: 360
    height: 48
    visible: false
    title: "taos-poc-bar"
    color: Theme.surface

    // Clock starts at x = 84, clear of the camera cut-out (x 0..80).
    Text {
        id: clock
        x: 84
        anchors.verticalCenter: parent.verticalCenter
        color: Theme.text
        font.pixelSize: 18
        text: PocConfig.clock
        Timer {
            interval: 1000; running: true; repeat: true
            onTriggered: PocConfig.refresh()
        }
    }
    Text {
        anchors.right: parent.right
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        horizontalAlignment: Text.AlignRight
        color: Theme.text
        font.pixelSize: 18
        text: PocConfig.battery
    }
}
