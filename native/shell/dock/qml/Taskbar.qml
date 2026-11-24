// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock

// The taskbar (layout.dock "taskbar"): a strip along a screen edge with the start button at the left end, the windows
// as labelled buttons, and, beside it, the tray that taos-bar draws (this surface leaves Theme.trayWidth free at the
// far end when there is no top bar). Colours, type and shape come from the Theme only.
Item {
    id: root
    readonly property string fontFamily: Theme.fontStack
    readonly property bool atBottom: Theme.dockEdge === "bottom"
    readonly property int bw: Theme.borderWidth
    readonly property int pad: Math.round((4) * Theme.spacingFactor)
    readonly property int gap: Math.round((4) * Theme.spacingFactor)
    readonly property int startW: Math.ceil(startLabelW) + 14 + Math.round((8) * Theme.spacingFactor) + Math.round((8) * Theme.spacingFactor) * 2 + Math.round((10) * Theme.spacingFactor)
    readonly property real startLabelW: startMetrics.implicitWidth
    readonly property int minW: Math.round(Theme.fontSize * 7) + Math.round((24) * Theme.spacingFactor)      // an icon and a few letters
    readonly property int maxW: Math.round(Theme.fontSize * 14) + Math.round((24) * Theme.spacingFactor)
    readonly property int moreW: Math.round(Theme.fontSize * 2.4) + Math.round((16) * Theme.spacingFactor)
    readonly property int innerTop: bw
    readonly property int innerH: height - 2 * bw
    property int focusIndex: -1          // keyboard: 0 start, 1..n windows, n+1 more
    property var plan: Dock.taskbarLayout(Math.max(0, width - 2 * pad - startW - gap - 1 - 2 * gap), minW, maxW, gap, moreW)
    Connections { target: Dock.taskbar; function onChanged() { root.refresh() } }
    onWidthChanged: refresh()
    Component.onCompleted: refresh()
    function refresh() { plan = Dock.taskbarLayout(Math.max(0, width - 2 * pad - startW - gap - 1 - 2 * gap), minW, maxW, gap, moreW) }

    Accessible.role: Accessible.ToolBar
    Accessible.name: qsTr("Taskbar")
    Text { id: startMetrics; visible: false; text: Theme.startLabel; font.family: root.fontFamily; font.pixelSize: Theme.fontSize; font.weight: Font.DemiBold }

    // surface
    Rectangle {
        anchors.fill: parent
        color: Theme.dockSurface === "solid" ? Theme.ground : Theme.dockSurface === "raised" ? Theme.surface
             : Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, Theme.variant === "light" ? 0.90 : 0.86)
    }
    Rectangle {   // hairline on the edge that faces the desktop, or the bevel
        visible: Theme.dockSurface !== "raised" && root.bw > 0
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: root.atBottom ? parent.top : undefined
        anchors.bottom: root.atBottom ? undefined : parent.bottom
        height: root.bw
        color: Theme.stroke
    }
    Rectangle { visible: Theme.dockSurface === "raised" && root.bw > 0; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: root.bw; color: Theme.bevelLight }
    Rectangle { visible: Theme.dockSurface === "raised" && root.bw > 0; anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom; height: root.bw; color: Theme.bevelDark }

    // keyboard (Super+D gives the taskbar the keyboard): arrows and Tab move, Enter or Space act, Menu opens the menu, Escape leaves
    focus: Dock.keyboardActive
    readonly property int stops: 1 + Dock.taskbar.count + (plan.hidden > 0 ? 1 : 0)
    function move(d) { if (stops > 0) focusIndex = (focusIndex + d + stops) % stops }
    function act(i) {
        if (i === 0) Dock.taskbarStart()
        else if (i <= Dock.taskbar.count) {
            var item = windowItem(i - 1)
            if (item) Dock.taskbarActivate(item.winId)
        } else moreBtn.activated()
    }
    function windowItem(pos) { for (var k = 0; k < rep.count; ++k) { var it = rep.itemAt(k); if (it && it.pos === pos) return it } return null }
    Connections {
        target: Dock
        function onKeyboardActiveChanged() {
            if (Dock.keyboardActive) { root.forceActiveFocus(); root.focusIndex = 0 } else root.focusIndex = -1
        }
    }
    Keys.onLeftPressed: move(-1)
    Keys.onRightPressed: move(1)
    Keys.onTabPressed: move(1)
    Keys.onBacktabPressed: move(-1)
    Keys.onPressed: (e) => {
        if (e.key === Qt.Key_Home) { focusIndex = 0; e.accepted = true }
        else if (e.key === Qt.Key_End) { focusIndex = stops - 1; e.accepted = true }
        else if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) { if (focusIndex >= 0) act(focusIndex); e.accepted = true }
        else if (e.key === Qt.Key_Menu && focusIndex > 0 && focusIndex <= Dock.taskbar.count) {
            var it = windowItem(focusIndex - 1)
            if (it) it.openMenuHere()
            e.accepted = true
        }
        else if (e.key === Qt.Key_Escape) { Dock.releaseKeyboard(); e.accepted = true }
    }
    HoverHandler { onHoveredChanged: { Dock.setPointerInside(hovered); if (!hovered) Dock.hideTip() } }

    // start button
    TaskbarButton {
        id: startBtn
        x: root.pad
        y: root.innerTop + Math.round((3) * Theme.spacingFactor)
        width: root.startW
        height: root.innerH - 2 * Math.round((3) * Theme.spacingFactor)
        label: Theme.startLabel
        bold: true
        showIcon: false
        accessibleText: qsTr("%1: open the launcher").arg(Theme.startLabel)
        kbd: Dock.keyboardActive && root.focusIndex === 0
        fontFamily: root.fontFamily
        onActivated: Dock.taskbarStart()
        onHoveredChanged: { if (hovered) tipTimer.restart(startBtn, qsTr("Open the launcher")); else Dock.hideTip() }
        // the mark: four squares
        Item {
            width: 14; height: 14
            Grid {
                columns: 2; spacing: 2
                Repeater { model: 4; Rectangle { width: 6; height: 6; color: index === 0 ? Theme.accent : Theme.text; radius: Math.round((1) * Theme.radiusScale) } }
            }
        }
    }
    Rectangle {   // separator
        x: root.pad + root.startW + root.gap
        y: root.innerTop + Math.round((6) * Theme.spacingFactor)
        width: 1
        height: root.innerH - 2 * Math.round((6) * Theme.spacingFactor)
        color: Theme.hairline
    }

    // window buttons
    Repeater {
        id: rep
        model: Dock.taskbar
        delegate: TaskbarButton {
            id: wb
            required property int index
            required property int windowId
            required property string title
            required property string icon
            required property bool focused
            required property bool minimised
            required property string accessibleText
            required property string tooltip
            readonly property int pos: index
            readonly property int winId: windowId
            readonly property int slot: index - root.plan.first
            visible: slot >= 0 && slot < root.plan.visible
            x: root.pad + root.startW + 2 * root.gap + 1 + Math.max(0, slot) * (root.plan.buttonWidth + root.gap)
            y: root.innerTop + Math.round((3) * Theme.spacingFactor)
            width: root.plan.buttonWidth
            height: root.innerH - 2 * Math.round((3) * Theme.spacingFactor)
            label: title
            iconName: icon
            active: focused
            minimised: wb.minimised
            kbd: Dock.keyboardActive && root.focusIndex === index + 1
            fontFamily: root.fontFamily
            function openMenuHere() { var c = wb.mapToItem(root, width / 2, height / 2); Dock.hideTip(); Dock.taskbarMenu(windowId, c.x, c.y) }
            onActivated: Dock.taskbarActivate(windowId)
            onContext: openMenuHere()
            onHoveredChanged: { if (hovered) tipTimer.restart(wb, tooltip); else Dock.hideTip() }
        }
    }
    // more windows than fit
    TaskbarButton {
        id: moreBtn
        visible: root.plan.hidden > 0
        x: root.pad + root.startW + 2 * root.gap + 1 + root.plan.visible * (root.plan.buttonWidth + root.gap)
        y: root.innerTop + Math.round((3) * Theme.spacingFactor)
        width: root.moreW
        height: root.innerH - 2 * Math.round((3) * Theme.spacingFactor)
        label: "+" + root.plan.hidden
        bold: true
        showIcon: false
        accessibleText: qsTr("%1 more windows").arg(root.plan.hidden)
        kbd: Dock.keyboardActive && root.focusIndex === root.stops - 1 && root.plan.hidden > 0
        fontFamily: root.fontFamily
        onActivated: { var c = moreBtn.mapToItem(root, width / 2, height / 2); Dock.hideTip(); Dock.taskbarOverflowMenu(root.plan.first, root.plan.visible, c.x, c.y) }
        onHoveredChanged: { if (hovered) tipTimer.restart(moreBtn, qsTr("%1 more windows").arg(root.plan.hidden)); else Dock.hideTip() }
    }
    // a tooltip after the pointer rests 500 ms (cleared on leave)
    Timer {
        id: tipTimer
        interval: 500
        property Item target
        property string text
        function restart(t, s) { target = t; text = s; start() }
        onTriggered: if (target) { var c = target.mapToItem(root, target.width / 2, target.height / 2); Dock.showTip(text, c.x, c.y) }
    }
}
