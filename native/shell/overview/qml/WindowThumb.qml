// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Overview

// One window: its live thumbnail with rounded corners, a hairline edge, and while no frame has
// arrived yet a quiet placeholder with the app tile. Sized by the caller (aspect handled inside).
Item {
    id: wt
    property string identifier: ""
    property string iconSource: ""
    property string appName: ""
    property real radius: 10
    property real aspect: 16 / 10          // width / height of the real window
    property alias hasImage: thumb.hasImage
    Accessible.ignored: true

    // the area the picture occupies: the largest box of the window's aspect inside this item
    readonly property real fitW: Math.min(width, height * aspect)
    readonly property real fitH: fitW / aspect

    Item {
        id: frame
        width: wt.fitW; height: wt.fitH
        anchors.centerIn: parent

        Rectangle {
            id: back
            anchors.fill: parent
            radius: wt.radius
            color: Style.surface
        }
        AppBadge {
            anchors.centerIn: parent
            size: Math.min(parent.width, parent.height) * 0.34
            iconSource: wt.iconSource
            appName: wt.appName
            opacity: thumb.hasImage ? 0 : 0.9
            Behavior on opacity { NumberAnimation { duration: Style.reduceMotion ? 0 : 160 } }
        }
        Item {
            id: picture
            anchors.fill: parent
            layer.enabled: true
            layer.smooth: true
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: maskItem
                maskThresholdMin: 0.5
                maskSpreadAtMin: 1.0
            }
            ThumbnailItem {
                id: thumb
                anchors.fill: parent
                identifier: wt.identifier
                opacity: hasImage ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Style.reduceMotion ? 0 : 140 } }
            }
        }
        Item {
            id: maskItem
            visible: false
            layer.enabled: true
            width: frame.width; height: frame.height
            Rectangle { anchors.fill: parent; radius: wt.radius; color: "black" }
        }
        Rectangle {
            anchors.fill: parent
            radius: wt.radius
            color: "transparent"
            border.width: 1
            border.color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.15)
        }
    }
}
