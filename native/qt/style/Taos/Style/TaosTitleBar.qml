// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as B
import Taos 1.0
import Taos.Style 1.0

// The taOS window header (32 px): close, minimise and full size buttons, the title centred, drag anywhere to move,
// double-click for full size, right-click for a small window menu. The buttons are on the left, or on the right
// in the order minimise, full size, close, as the theme and the person choose (TaosStyle.windowButtons).
// The compositor draws no title bar for taOS apps; this is it.
Item {
    id: bar
    property var window: null           // the QQuickWindow
    property string title: window ? window.title : ""
    readonly property bool buttonsRight: TaosStyle.windowButtons === "right"
    readonly property bool active: window ? window.active : true
    readonly property bool compact: width < 220
    property alias control: control
    readonly property int barHeight: 32
    height: barHeight
    implicitHeight: barHeight

    WindowControl { id: control; window: bar.window }

    Rectangle {
        anchors.fill: parent
        color: TaosStyle.surface
        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: TaosStyle.hairline }
    }

    // Move and full size. Behind the buttons so they win their own clicks.
    MouseArea {
        id: drag
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: (m) => { if (m.button === Qt.LeftButton) pressPos = Qt.point(m.x, m.y) }
        property point pressPos
        onPositionChanged: (m) => {
            if ((m.buttons & Qt.LeftButton) && bar.window && (Math.abs(m.x - pressPos.x) + Math.abs(m.y - pressPos.y)) > 4) {
                pressPos = Qt.point(1e6, 1e6)
                bar.window.startSystemMove()
            }
        }
        onDoubleClicked: (m) => { if (m.button === Qt.LeftButton) control.toggleFull() }
        onClicked: (m) => { if (m.button === Qt.RightButton) menu.popup(m.x, m.y) }
    }

    Text {
        id: titleText
        anchors.verticalCenter: parent.verticalCenter
        x: Math.max(buttons.width + 24, Math.min(bar.width - width - buttons.width - 24, (bar.width - width) / 2))
        width: Math.min(implicitWidth, bar.width - 2 * (buttons.width + 24))
        text: bar.title
        elide: Text.ElideRight
        color: bar.active ? TaosStyle.text : TaosStyle.textSecondary
        font.pixelSize: TaosStyle.bodySize
        font.weight: Font.Medium
        visible: bar.width - 2 * (buttons.width + 24) > 40
        horizontalAlignment: Text.AlignHCenter
        Accessible.ignored: true
    }

    Row {
        id: buttons
        spacing: 4
        anchors.verticalCenter: parent.verticalCenter
        x: bar.buttonsRight ? bar.width - width - 8 : 8
        layoutDirection: Qt.LeftToRight
        HoverHandler { id: groupHover }
        Repeater {
            model: bar.buttonsRight ? ["minimise", "full", "close"] : ["close", "minimise", "full"]
            TitleButton {
                required property string modelData
                glyph: modelData
                danger: modelData === "close"
                groupHovered: groupHover.hovered
                windowActive: bar.active
                label: modelData === "close" ? qsTr("Close window") : (modelData === "minimise" ? qsTr("Minimise window") : qsTr("Full size"))
                onActivated: {
                    if (modelData === "close") control.close()
                    else if (modelData === "minimise") control.minimise()
                    else control.toggleFull()
                }
            }
        }
    }

    B.Menu {
        id: menu
        modal: false
        padding: 4
        implicitWidth: 168
        background: Rectangle {
            color: TaosStyle.raised
            radius: 8
            border.width: 1
            border.color: TaosStyle.hairline
        }
        component Entry: B.MenuItem {
            id: item
            property bool danger: false
            implicitHeight: 28
            contentItem: Text {
                text: item.text
                color: item.danger && item.highlighted ? TaosStyle.danger : TaosStyle.text
                font.pixelSize: TaosStyle.bodySize
                verticalAlignment: Text.AlignVCenter
                leftPadding: 8
            }
            background: Rectangle {
                radius: 5
                color: item.highlighted ? TaosStyle.selection : "transparent"
            }
        }
        Entry { text: qsTr("Minimise"); onTriggered: control.minimise() }
        Entry { text: qsTr("Full size"); onTriggered: control.toggleFull() }
        Entry { text: qsTr("Close"); danger: true; onTriggered: control.close() }
    }
}
