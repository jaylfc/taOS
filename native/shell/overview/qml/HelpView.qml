// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Overview

// The keyboard shortcuts overlay (Super+Ctrl+Shift+H): grouped, searchable as you type, generated from the bindings
// sway loaded. Esc or a click closes it. On a narrow screen it is one column and lists touch gestures instead.
Item {
    id: hv
    property real progress: 0
    readonly property string font: Style.fontStack
    readonly property real fs: Style.fontSize > 0 ? Style.fontSize : 14
    readonly property real u: 8
    readonly property bool compact: width < 760 || height > width * 1.1
    readonly property var groups: Overview.helpGroups
    function dur(ms) { return Style.reduceMotion ? 0 : ms; }
    onCompactChanged: Overview.setHelpTouch(compact)
    Component.onCompleted: Overview.setHelpTouch(compact)

    // panel and columns
    readonly property real panelW: Math.min(width - 4 * u, compact ? width : 1480)
    readonly property real panelH: compact ? height - 4 * u : Math.min(height - 8 * u, 1000)
    readonly property real innerW: panelW - 6 * u
    readonly property int colCount: compact ? 1 : Math.max(1, Math.min(4, Math.floor(innerW / (46 * u))))
    readonly property real colGap: 4 * u
    readonly property real colW: (innerW - (colCount - 1) * colGap) / colCount
    // balance the groups over the columns by their height
    readonly property var columns: {
        const cols = [];
        const weights = [];
        for (let i = 0; i < colCount; ++i) { cols.push([]); weights.push(0); }
        for (let g = 0; g < groups.length; ++g) {
            let best = 0;
            for (let i = 1; i < colCount; ++i) if (weights[i] < weights[best]) best = i;
            cols[best].push(groups[g]);
            weights[best] += groups[g].rows.length + 3;
        }
        return cols;
    }

    function scrollBy(dy) { flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, flick.contentY + dy)); }
    function scrollTo(top) { flick.contentY = top ? 0 : Math.max(0, flick.contentHeight - flick.height); }

    Item {
        id: panel
        width: hv.panelW; height: hv.panelH
        x: Math.round((hv.width - width) / 2); y: Math.round((hv.height - height) / 2)
        opacity: hv.progress
        scale: 0.97 + 0.03 * hv.progress
        Glass { anchors.fill: parent; radius: Style.radius(hv.compact ? 14 : 22); shadow: false; tintBoost: 0.08 }
        Accessible.role: Accessible.Pane
        Accessible.name: qsTr("Keyboard shortcuts")

        MouseArea { anchors.fill: parent; onClicked: Overview.cancel() }

        // header: title, search
        Item {
            id: head
            x: 3 * hv.u; y: 2 * hv.u
            width: hv.innerW; height: 6 * hv.u
            Text {
                id: title
                anchors.verticalCenter: parent.verticalCenter
                text: hv.compact ? qsTr("Gestures") : qsTr("Keyboard shortcuts")
                color: Style.text
                font.family: Style.fontDisplayStack
                font.pixelSize: hv.fs + 8
                font.weight: Font.DemiBold
                font.capitalization: Style.upperTitles ? Font.AllUppercase : Font.MixedCase
                renderType: Text.NativeRendering
                Accessible.role: Accessible.Heading
            }
            Rectangle {
                id: field
                anchors.verticalCenter: parent.verticalCenter
                anchors.right: parent.right
                width: hv.compact ? parent.width - title.implicitWidth - 2 * hv.u : Math.min(parent.width - title.implicitWidth - 4 * hv.u, 44 * hv.u)
                height: 5 * hv.u
                radius: height / 2
                color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.08)
                border.width: 1; border.color: Style.glassStroke
                Item {
                    x: 2 * hv.u; anchors.verticalCenter: parent.verticalCenter; width: 2 * hv.u; height: 2 * hv.u; opacity: 0.7
                    Rectangle { width: 12; height: 12; radius: 6; color: "transparent"; border.width: 2; border.color: Style.textSecondary }
                    Rectangle { x: 11; y: 13; width: 7; height: 2; radius: 1; rotation: 45; transformOrigin: Item.Left; color: Style.textSecondary }
                }
                Text {
                    id: q
                    x: 5 * hv.u
                    width: parent.width - 8 * hv.u
                    anchors.verticalCenter: parent.verticalCenter
                    elide: Text.ElideLeft
                    text: Overview.filterText.length > 0 ? Overview.filterText : qsTr("Type to search")
                    color: Overview.filterText.length > 0 ? Style.text : Style.textSecondary
                    font.family: hv.font
                    font.pixelSize: hv.fs + 1
                    renderType: Text.NativeRendering
                    Accessible.name: Overview.filterText.length > 0 ? qsTr("Search: %1").arg(Overview.filterText) : qsTr("Search shortcuts")
                }
                Rectangle {
                    id: caret
                    visible: Overview.filterText.length > 0
                    x: Math.min(q.x + q.implicitWidth + 2, q.x + q.width)
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2; height: hv.fs + 4
                    color: Style.accent
                    SequentialAnimation on opacity {
                        running: caret.visible && !Style.reduceMotion && hv.progress > 0.99
                        loops: Animation.Infinite
                        NumberAnimation { to: 0; duration: 500 }
                        NumberAnimation { to: 1; duration: 500 }
                    }
                }
            }
        }
        Rectangle { x: 3 * hv.u; y: head.y + head.height + hv.u; width: hv.innerW; height: 1; color: Style.hairline }

        Flickable {
            id: flick
            x: 3 * hv.u
            y: head.y + head.height + 3 * hv.u
            width: hv.innerW
            height: panel.height - y - (hv.compact ? 2 * hv.u : 6 * hv.u)
            contentWidth: width
            contentHeight: body.height
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            Behavior on contentY { enabled: !flick.dragging && !flick.flicking; NumberAnimation { duration: hv.dur(140); easing.type: Easing.OutCubic } }
            // clicks on the content close it, like clicks anywhere else
            MouseArea { width: flick.contentWidth; height: Math.max(flick.contentHeight, flick.height); onClicked: Overview.cancel() }

            Row {
                id: body
                spacing: hv.colGap
                Repeater {
                    model: hv.columns
                    delegate: Column {
                        required property var modelData
                        width: hv.colW
                        spacing: 3 * hv.u
                        Repeater {
                            model: modelData
                            delegate: Column {
                                id: grp
                                required property var modelData
                                width: hv.colW
                                spacing: hv.u / 2
                                Text {
                                    text: grp.modelData.title
                                    color: Style.accent
                                    font.family: Style.fontDisplayStack
                                    font.pixelSize: hv.fs
                                    font.weight: Font.DemiBold
                                    font.capitalization: Style.upperTitles ? Font.AllUppercase : Font.MixedCase
                                    font.letterSpacing: Style.titleTracking
                                    renderType: Text.NativeRendering
                                    bottomPadding: hv.u / 2
                                    Accessible.role: Accessible.Heading
                                }
                                Repeater {
                                    model: grp.modelData.rows
                                    delegate: Item {
                                        id: row
                                        required property var modelData
                                        width: hv.colW
                                        height: Math.max(4 * hv.u, rowText.implicitHeight + hv.u, caps.height + hv.u)
                                        Accessible.role: Accessible.ListItem
                                        Accessible.name: modelData.text + ", " + modelData.keys.join(" ")
                                        Rectangle { anchors.fill: parent; radius: Style.radius(6); color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.05); visible: rowHover.containsMouse }
                                        MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true; onClicked: Overview.cancel() }
                                        Text {
                                            id: rowText
                                            anchors.verticalCenter: parent.verticalCenter
                                            x: hv.u
                                            width: parent.width - caps.width - 3 * hv.u
                                            text: row.modelData.text
                                            wrapMode: Text.WordWrap
                                            color: row.modelData.described ? Style.text : Style.textSecondary
                                            font.family: row.modelData.described ? hv.font : Style.fontDataStack
                                            font.pixelSize: hv.fs
                                            renderType: Text.NativeRendering
                                        }
                                        Row {
                                            id: caps
                                            anchors.verticalCenter: parent.verticalCenter
                                            anchors.right: parent.right
                                            anchors.rightMargin: hv.u
                                            spacing: hv.u / 2
                                            Repeater {
                                                model: row.modelData.keys
                                                delegate: Rectangle {
                                                    id: cap
                                                    required property string modelData
                                                    height: 3 * hv.u
                                                    width: Math.max(height, capText.implicitWidth + 2 * hv.u)
                                                    radius: Style.radius(6)
                                                    color: Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.10)
                                                    border.width: 1
                                                    border.color: Style.glassStroke
                                                    Text {
                                                        id: capText
                                                        anchors.centerIn: parent
                                                        text: cap.modelData
                                                        color: Style.text
                                                        font.family: Style.fontDataStack
                                                        font.pixelSize: hv.fs - 1
                                                        font.weight: Font.Medium
                                                        renderType: Text.NativeRendering
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Text {
                visible: hv.groups.length === 0
                anchors.horizontalCenter: parent.horizontalCenter
                y: 8 * hv.u
                text: qsTr("No shortcut matches")
                color: Style.textSecondary
                font.family: hv.font
                font.pixelSize: hv.fs + 4
                renderType: Text.NativeRendering
            }
        }
        // the footer says what an unexplained row is, and how to leave
        Text {
            visible: !hv.compact
            x: 3 * hv.u
            y: panel.height - 4 * hv.u
            width: hv.innerW
            text: Overview.helpUndescribed > 0
                  ? qsTr("%1 of your own shortcuts are shown as written.    Up and Down scroll    Esc closes").arg(Overview.helpUndescribed)
                  : qsTr("Up and Down scroll    Esc closes")
            color: Style.textSecondary
            font.family: hv.font
            font.pixelSize: hv.fs - 2
            renderType: Text.NativeRendering
            Accessible.ignored: true
        }
    }
}
