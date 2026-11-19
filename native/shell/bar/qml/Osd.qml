import QtQuick
import Taos.Bar

// The level flash for the volume keys: a small frosted pill at the bottom
// centre of the main screen, shown for a moment, then gone. It never takes
// input. With reduced motion it appears and disappears without fading.
Item {
    id: root
    property string kind: "volume"
    property int percent: 0
    property bool muted: false
    property bool noDevice: false
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"

    function flash(k, p, m) {
        kind = k
        noDevice = p < 0
        percent = Math.max(0, p)
        muted = m
        hideTimer.restart()
        card.opacity = 1
        Accessible.name = noDevice ? (k === "mic" ? qsTr("No microphone") : qsTr("No sound output"))
                                   : (k === "mic" ? (m ? qsTr("Microphone muted") : qsTr("Microphone on"))
                                                  : (m ? qsTr("Volume muted") : qsTr("Volume %1 percent").arg(p)))
    }
    Accessible.role: Accessible.AlertMessage

    Timer { id: hideTimer; interval: 1400; onTriggered: card.opacity = 0 }  // runs only while the flash is up

    Item {
        id: card
        anchors.fill: parent
        anchors.margins: 12
        opacity: 0
        Behavior on opacity { enabled: !Theme.reduceMotion; NumberAnimation { duration: card.opacity > 0 ? 120 : 260; easing.type: Easing.OutCubic } }
        MenuPanel { anchors.fill: parent; radius: height / 2 }
        Row {
            anchors.centerIn: parent
            spacing: 12
            Item {
                width: 22; height: 20
                anchors.verticalCenter: parent.verticalCenter
                VolumeIcon {
                    visible: root.kind === "volume"
                    anchors.centerIn: parent
                    level: root.noDevice ? "none" : root.muted || root.percent === 0 ? "muted" : root.percent < 34 ? "low" : root.percent < 67 ? "medium" : "high"
                }
                DeviceGlyph { visible: root.kind === "mic"; anchors.centerIn: parent; kind: "mic"; color: root.muted || root.noDevice ? Theme.danger : Theme.text }
                Rectangle {
                    visible: root.kind === "mic" && (root.muted || root.noDevice)
                    anchors.centerIn: parent; width: 22; height: 1.5; radius: 1; rotation: -40; color: Theme.danger
                }
            }
            Item {
                width: 124; height: 6
                anchors.verticalCenter: parent.verticalCenter
                visible: root.kind === "volume"
                Rectangle { anchors.fill: parent; radius: 3; color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, 0.22) }
                Rectangle {
                    height: parent.height; radius: 3
                    width: root.noDevice || root.muted ? 0 : parent.width * root.percent / 100
                    color: Theme.text
                    Behavior on width { enabled: !Theme.reduceMotion; NumberAnimation { duration: 90 } }
                }
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.noDevice ? (root.kind === "mic" ? qsTr("No microphone") : qsTr("No sound output"))
                                    : root.kind === "mic" ? (root.muted ? qsTr("Microphone off") : qsTr("Microphone on"))
                                                          : (root.muted ? qsTr("Muted") : root.percent + "%")
                color: Theme.text
                font.family: root.fontFamily; font.pixelSize: 13; font.weight: Font.Medium
                font.features: { "tnum": 1 }
                renderType: Text.NativeRendering
            }
        }
    }
}
