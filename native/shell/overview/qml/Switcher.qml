// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Overview

// Alt+Tab: a centred glass panel with a row of live thumbnails, most recent window first.
Item {
    id: sw
    property real progress: 0
    // one thumbnail height for the row; each card's width follows its window's aspect ratio
    readonly property real thumbH: Math.round(Math.max(150, Math.min(250, height * 0.17)))
    readonly property real maxCardW: Math.round(thumbH * 1.9)
    readonly property real cardH: thumbH + 50
    readonly property real inset: 8          // room around a card for the selection highlight
    readonly property real pad: 18
    readonly property real gap: 6
    readonly property string font: "Inter"

    function cardWidth(aspect) { return Math.round(Math.max(thumbH * 0.95, Math.min(maxCardW, thumbH * aspect))); }
    readonly property real rowWidth: {
        const a = Overview.aspects;
        let w = 0;
        for (let i = 0; i < a.length; ++i) w += cardWidth(a[i]) + 2 * inset + (i > 0 ? gap : 0);
        return w;
    }

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }
    onMaxCardWChanged: Overview.setCardSize(Qt.size(maxCardW, thumbH))
    Component.onCompleted: Overview.setCardSize(Qt.size(maxCardW, thumbH))

    Item {
        id: holder
        width: Math.min(sw.width - 80, sw.rowWidth + 2 * sw.pad)
        height: sw.cardH + 2 * sw.inset + 2 * sw.pad
        x: Math.round((sw.width - width) / 2)
        y: Math.round((sw.height - height) / 2 - sw.height * 0.03)
        opacity: sw.progress
        scale: 0.955 + 0.045 * sw.progress
        transformOrigin: Item.Center

        Glass { anchors.fill: parent; radius: 26 }

        ListView {
            id: list
            anchors.fill: parent
            anchors.margins: sw.pad
            orientation: ListView.Horizontal
            spacing: sw.gap
            clip: true
            cacheBuffer: 100000
            model: Overview.windows
            currentIndex: Overview.selectedIndex
            boundsBehavior: Flickable.StopAtBounds
            highlightRangeMode: ListView.ApplyRange
            preferredHighlightBegin: width * 0.18
            preferredHighlightEnd: width * 0.82
            highlightMoveDuration: sw.dur(190)
            highlightMoveVelocity: -1
            highlightFollowsCurrentItem: true
            Accessible.role: Accessible.List
            Accessible.name: qsTr("Open windows")

            highlight: Rectangle {
                z: 0
                radius: 18
                color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.18 : 0.15)
                border.width: 1
                border.color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, Style.dark ? 0.25 : 0.20)
            }

            delegate: Item {
                id: card
                required property int index
                required property int conId
                required property string title
                required property string appName
                required property string iconSource
                required property string identifier
                required property bool urgent
                required property bool minimised
                required property string accessibleText
                required property rect rect
                readonly property bool selected: index === Overview.selectedIndex
                readonly property real aspect: rect.height > 0 ? rect.width / rect.height : 1.6
                readonly property real cw: sw.cardWidth(aspect)
                width: cw + 2 * sw.inset
                height: sw.cardH + 2 * sw.inset
                z: 1
                Accessible.role: Accessible.ListItem
                Accessible.name: accessibleText
                Accessible.selected: selected
                Accessible.focusable: true

                Item {
                    x: sw.inset; y: sw.inset
                    width: card.cw; height: sw.cardH
                    opacity: card.minimised ? 0.5 : 1
                    WindowThumb {
                        width: card.cw; height: sw.thumbH
                        identifier: card.identifier
                        iconSource: card.iconSource
                        appName: card.appName
                        aspect: card.aspect
                        radius: 10
                        scale: card.selected ? 1.0 : 0.97
                        Behavior on scale { NumberAnimation { duration: sw.dur(160); easing.type: Easing.OutCubic } }
                    }
                    AppBadge {
                        x: Math.round((card.cw - size) / 2)
                        y: sw.thumbH - 18
                        size: 40
                        iconSource: card.iconSource
                        appName: card.appName
                    }
                    Rectangle {
                        visible: card.urgent
                        x: card.cw - 18; y: 4
                        width: 10; height: 10; radius: 5
                        color: Style.warning
                        border.width: 1; border.color: Style.stroke
                    }
                    Rectangle {
                        visible: card.minimised
                        x: Math.round((card.cw - width) / 2); y: 8
                        width: minLabel.implicitWidth + 20; height: 22; radius: 11
                        color: Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, 0.82)
                        border.width: 1; border.color: Style.stroke
                        Text {
                            id: minLabel
                            anchors.centerIn: parent
                            text: qsTr("minimised")
                            color: Style.text
                            font.family: sw.font
                            font.pixelSize: 12
                            font.weight: Font.Medium
                            renderType: Text.NativeRendering
                        }
                    }
                    Text {
                        x: 4
                        y: sw.thumbH + 28
                        width: card.cw - 8
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        text: card.title.length ? card.title : card.appName
                        color: card.selected ? Style.text : Style.textSecondary
                        font.family: sw.font
                        font.pixelSize: 13
                        font.weight: card.selected ? Font.DemiBold : Font.Medium
                        renderType: Text.NativeRendering
                        Behavior on color { ColorAnimation { duration: sw.dur(120) } }
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onPositionChanged: Overview.selectedIndex = card.index
                    onClicked: Overview.focusWindow(card.conId)
                }
            }
        }
    }
}
