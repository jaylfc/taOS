// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Overview

// Super tap: the current workspace's windows packed as live thumbnails, a strip of live workspace
// miniatures on top, and type-to-filter across every workspace.
Item {
    id: ov
    property real progress: 0
    readonly property string font: "Inter"
    readonly property real stripH: Math.round(Math.max(104, Math.min(160, height * 0.15)))
    readonly property real miniH: stripH - 40
    readonly property real areaX: Math.round(Math.max(48, width * 0.035))
    readonly property real areaY: stripH + 34 + 54
    readonly property real areaW: width - 2 * areaX
    readonly property real areaH: Math.max(100, height - areaY - 64)
    property bool settled: false

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }
    function pushArea() { Overview.setOverviewArea(Qt.rect(areaX, areaY, areaW, areaH)); }
    onAreaWChanged: pushArea()
    onAreaHChanged: pushArea()
    onAreaYChanged: pushArea()
    Component.onCompleted: { pushArea(); Overview.setMiniSize(Qt.size(Math.round(miniH * 1.78), miniH)); }
    onMiniHChanged: Overview.setMiniSize(Qt.size(Math.round(miniH * 1.78), miniH))
    onProgressChanged: if (progress <= 0.001) settled = false
    Timer { running: ov.progress >= 0.999 && !ov.settled; interval: 60; onTriggered: ov.settled = true }

    // ---- workspace strip -------------------------------------------------------------------
    Item {
        id: strip
        y: 26
        height: ov.stripH
        width: Math.min(ov.width - 60, row.width + 36)
        x: Math.round((ov.width - width) / 2)
        opacity: ov.progress
        transform: Translate { y: (1 - ov.progress) * -14 }

        Glass { anchors.fill: parent; radius: 22 }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 16
            Repeater {
                model: Overview.workspaces
                delegate: Item {
                    id: mini
                    required property var modelData
                    readonly property real aspect: modelData.size.height > 0 ? modelData.size.width / modelData.size.height : 1.6
                    readonly property bool current: modelData.current
                    width: Math.round(ov.miniH * aspect)
                    height: ov.miniH + 20
                    Accessible.role: Accessible.Button
                    Accessible.name: modelData.isNew ? qsTr("New workspace %1").arg(modelData.name)
                                                     : qsTr("Workspace %1, %2 windows").arg(modelData.name).arg(modelData.count)
                    Accessible.onPressAction: Overview.goToWorkspace(modelData.name, modelData.num)

                    Item {
                        id: tile
                        width: mini.width; height: ov.miniH
                        scale: ma.containsMouse ? 1.05 : 1.0
                        Behavior on scale { NumberAnimation { duration: ov.dur(140); easing.type: Easing.OutCubic } }
                        Rectangle {
                            anchors.fill: parent
                            radius: 8
                            color: Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, Style.dark ? 0.70 : 0.80)
                        }
                        Repeater {
                            model: mini.modelData.windows
                            delegate: WindowThumb {
                                required property var modelData
                                readonly property var ws: mini.modelData
                                x: modelData.rect.x / ws.size.width * tile.width
                                y: modelData.rect.y / ws.size.height * tile.height
                                width: modelData.rect.width / ws.size.width * tile.width
                                height: modelData.rect.height / ws.size.height * tile.height
                                identifier: modelData.identifier
                                aspect: modelData.rect.height > 0 ? modelData.rect.width / modelData.rect.height : 1.6
                                radius: 2
                            }
                        }
                        Rectangle {
                            anchors.fill: parent
                            radius: 8
                            color: "transparent"
                            border.width: mini.current ? 2 : 1
                            border.color: mini.current ? Style.accent : (ma.containsMouse ? Style.textSecondary : Style.glassStroke)
                        }
                        // the "new workspace" tile: a plus
                        Item {
                            visible: mini.modelData.isNew
                            anchors.centerIn: parent
                            width: 22; height: 22
                            Rectangle { anchors.centerIn: parent; width: 22; height: 2; radius: 1; color: Style.textSecondary }
                            Rectangle { anchors.centerIn: parent; width: 2; height: 22; radius: 1; color: Style.textSecondary }
                        }
                        MouseArea {
                            id: ma
                            anchors.fill: parent
                            hoverEnabled: true
                            onClicked: Overview.goToWorkspace(mini.modelData.name, mini.modelData.num)
                        }
                    }
                    Text {
                        y: ov.miniH + 3
                        width: mini.width
                        horizontalAlignment: Text.AlignHCenter
                        text: mini.modelData.isNew ? qsTr("New") : mini.modelData.name
                        color: mini.current ? Style.text : Style.textSecondary
                        font.family: ov.font
                        font.pixelSize: 12
                        font.weight: mini.current ? Font.DemiBold : Font.Medium
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
                    }
                }
            }
        }
    }

    // ---- search pill ---------------------------------------------------------------------------
    Item {
        id: pill
        visible: Overview.filterText.length > 0
        width: Math.max(200, query.implicitWidth + 56)
        height: 40
        y: strip.y + strip.height + 18
        x: Math.round((ov.width - width) / 2)
        opacity: ov.progress
        Glass { anchors.fill: parent; radius: 20; shadow: false }
        Text {
            id: query
            anchors.centerIn: parent
            text: Overview.filterText
            color: Style.text
            font.family: ov.font
            font.pixelSize: 15
            font.weight: Font.Medium
            renderType: Text.NativeRendering
        }
        Accessible.role: Accessible.StaticText
        Accessible.name: qsTr("Filter: %1").arg(Overview.filterText)
    }
    Text {
        visible: Overview.filterText.length === 0
        y: strip.y + strip.height + 26
        anchors.horizontalCenter: parent.horizontalCenter
        opacity: ov.progress * 0.62
        text: qsTr("Type to search all windows")
        color: Style.textSecondary
        font.family: ov.font
        font.pixelSize: 13
        renderType: Text.NativeRendering
        Accessible.ignored: true
    }
    Text {
        visible: Overview.filterText.length > 0 && Overview.layout && Object.keys(Overview.layout).length === 0
        anchors.centerIn: parent
        text: qsTr("No windows match")
        color: Style.textSecondary
        font.family: ov.font
        font.pixelSize: 20
        font.weight: Font.Medium
        renderType: Text.NativeRendering
    }

    // ---- the windows ---------------------------------------------------------------------------
    Item {
        id: field
        anchors.fill: parent
        opacity: ov.progress
        scale: 0.96 + 0.04 * ov.progress
        transformOrigin: Item.Center

        Repeater {
            model: Overview.windows
            delegate: Item {
                id: win
                required property int index
                required property int conId
                required property string title
                required property string appName
                required property string iconSource
                required property string identifier
                required property bool urgent
                required property bool minimised
                required property string accessibleText
                required property string workspace
                required property rect rect
                readonly property var lr: Overview.layout[conId]
                readonly property bool present: lr !== undefined
                readonly property bool selected: index === Overview.selectedIndex
                readonly property bool hot: selected || winArea.containsMouse
                x: present ? lr.x : 0
                y: present ? lr.y : 0
                width: present ? lr.width : 0
                height: present ? lr.height : 0
                visible: opacity > 0.01
                opacity: present ? 1 : 0
                z: hot ? 5 : 1
                enabled: present
                Behavior on x { enabled: ov.settled; NumberAnimation { duration: ov.dur(240); easing.type: Easing.OutCubic } }
                Behavior on y { enabled: ov.settled; NumberAnimation { duration: ov.dur(240); easing.type: Easing.OutCubic } }
                Behavior on width { enabled: ov.settled; NumberAnimation { duration: ov.dur(240); easing.type: Easing.OutCubic } }
                Behavior on height { enabled: ov.settled; NumberAnimation { duration: ov.dur(240); easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: ov.dur(180) } }
                Accessible.role: Accessible.ListItem
                Accessible.name: accessibleText
                Accessible.selected: selected
                Accessible.focusable: true
                Accessible.onPressAction: Overview.focusWindow(conId)

                Item {
                    id: lift
                    anchors.fill: parent
                    scale: win.hot ? 1.035 : 1.0
                    opacity: win.minimised ? 0.5 : 1
                    transformOrigin: Item.Center
                    Behavior on scale { NumberAnimation { duration: ov.dur(150); easing.type: Easing.OutCubic } }
                    WindowThumb {
                        anchors.fill: parent
                        identifier: win.identifier
                        iconSource: win.iconSource
                        appName: win.appName
                        aspect: win.rect.height > 0 ? win.rect.width / win.rect.height : 1.6
                        radius: 12
                    }
                    Rectangle {
                        anchors.fill: parent
                        radius: 12
                        color: "transparent"
                        border.width: win.selected ? 3 : (winArea.containsMouse ? 2 : 0)
                        border.color: Style.accent
                        visible: border.width > 0
                    }
                    Rectangle {
                        visible: win.minimised
                        x: Math.round((parent.width - width) / 2); y: 10
                        width: minLabel.implicitWidth + 22; height: 24; radius: 12
                        color: Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, 0.82)
                        border.width: 1; border.color: Style.stroke
                        Text {
                            id: minLabel
                            anchors.centerIn: parent
                            text: qsTr("minimised")
                            color: Style.text
                            font.family: ov.font
                            font.pixelSize: 12
                            font.weight: Font.Medium
                            renderType: Text.NativeRendering
                        }
                    }
                    Rectangle {
                        visible: win.urgent
                        x: parent.width - 20; y: 8
                        width: 12; height: 12; radius: 6
                        color: Style.warning
                    }
                    // title pill, on hover or selection
                    Item {
                        id: tp
                        width: Math.min(parent.width - 24, label.implicitWidth + 36)
                        height: 30
                        x: Math.round((parent.width - width) / 2)
                        y: parent.height - height - 36
                        opacity: win.hot ? 1 : 0
                        visible: opacity > 0.01
                        Behavior on opacity { NumberAnimation { duration: ov.dur(140) } }
                        Glass { anchors.fill: parent; radius: 15; shadow: false; tintBoost: 0.1 }
                        Text {
                            id: label
                            anchors.centerIn: parent
                            width: Math.min(implicitWidth, tp.width - 28)
                            elide: Text.ElideRight
                            text: win.title.length ? win.title : win.appName
                            color: Style.text
                            font.family: ov.font
                            font.pixelSize: 13
                            font.weight: Font.Medium
                            renderType: Text.NativeRendering
                        }
                    }
                    AppBadge {
                        x: Math.round((parent.width - size) / 2)
                        y: parent.height - size * 0.45
                        size: Math.max(34, Math.min(56, parent.height * 0.2))
                        iconSource: win.iconSource
                        appName: win.appName
                    }
                }
                MouseArea {
                    id: winArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onPositionChanged: Overview.selectedIndex = win.index
                    onClicked: Overview.focusWindow(win.conId)
                }
            }
        }
    }
}
