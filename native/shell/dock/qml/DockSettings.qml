import QtQuick
import QtQuick.Effects
import Taos.Dock

// The Dock settings popover: position, floating or attached, auto-hide, size, magnification and the
// tiling and output options. Every choice applies at once (the dock follows dock.json live).
FocusScope {
    id: root
    property real anchorX: 0
    property real anchorY: 0
    property string edge: "bottom"
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    readonly property bool dark: Theme.variant !== "light"
    focus: true
    Accessible.role: Accessible.Pane
    Accessible.name: "Dock settings"
    Keys.onEscapePressed: Dock.closeMenu()
    Component.onCompleted: { forceActiveFocus(); if (!Theme.reduceMotion) openAnim.start(); else panel.opacity = 1 }

    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onPressed: Dock.closeMenu(); Accessible.ignored: true }

    // A row of exclusive choices.
    component Choice: Item {
        id: ch
        property string label
        property string key
        property var options: []        // [{text, value}]
        readonly property string current: String(DockSettings[key])
        width: parent ? parent.width : 0
        height: 52
        Text {
            text: ch.label
            color: Theme.textSecondary
            font.family: root.fontFamily
            font.pixelSize: 12
            x: 4
        }
        Row {
            y: 20
            spacing: 4
            Repeater {
                model: ch.options
                delegate: Rectangle {
                    required property var modelData
                    readonly property bool on: ch.current === modelData.value
                    width: (ch.width - 4 * (ch.options.length - 1)) / ch.options.length
                    height: 28
                    radius: 8
                    color: on ? Theme.selection : (m.containsMouse ? Theme.hover : Theme.raised)
                    border.width: activeFocus ? 2 : 1
                    border.color: activeFocus ? Theme.focusRing : Theme.hairline
                    activeFocusOnTab: true
                    Accessible.role: Accessible.RadioButton
                    Accessible.name: ch.label + ": " + modelData.text
                    Accessible.checked: on
                    Accessible.onPressAction: Dock.setSetting(ch.key, modelData.value)
                    Keys.onReturnPressed: Dock.setSetting(ch.key, modelData.value)
                    Keys.onSpacePressed: Dock.setSetting(ch.key, modelData.value)
                    Text {
                        anchors.centerIn: parent
                        text: modelData.text
                        color: Theme.text
                        font.family: root.fontFamily
                        font.pixelSize: 13
                        font.weight: on ? Font.DemiBold : Font.Normal
                    }
                    MouseArea { id: m; anchors.fill: parent; hoverEnabled: true; onClicked: Dock.setSetting(ch.key, modelData.value) }
                }
            }
        }
    }

    Item {
        id: panel
        width: 300
        height: col.implicitHeight + 24
        opacity: 0
        x: edge === "left" ? root.anchorX + Dock.stripRect.width / 2 + 24
         : edge === "right" ? root.anchorX - width - Dock.stripRect.width / 2 - 24
         : Math.max(8, Math.min(root.width - width - 8, root.anchorX - width / 2))
        y: edge === "bottom" ? root.anchorY - Dock.stripRect.height / 2 - 30 - height
         : Math.max(8, Math.min(root.height - height - 8, root.anchorY - height / 2))
        MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons }
        NumberAnimation { id: openAnim; target: panel; property: "opacity"; from: 0; to: 1; duration: 140; easing.type: Easing.OutCubic }

        Rectangle { id: shadowSource; anchors.fill: parent; radius: 14; color: Theme.ground; visible: false; layer.enabled: true }
        MultiEffect {
            anchors.fill: shadowSource
            source: shadowSource
            autoPaddingEnabled: true
            shadowEnabled: true
            shadowColor: "black"
            shadowOpacity: root.dark ? 0.45 : 0.28
            shadowBlur: 1.0
            shadowVerticalOffset: 8
        }
        Rectangle {
            anchors.fill: parent
            radius: 14
            color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, root.dark ? 0.90 : 0.94)
            border.width: 1
            border.color: Theme.glassStroke
        }
        Column {
            id: col
            x: 14
            y: 12
            width: parent.width - 28
            spacing: 6
            Text { text: "Dock settings"; color: Theme.text; font.family: root.fontFamily; font.pixelSize: 15; font.weight: Font.DemiBold; bottomPadding: 6 }
            Choice { label: "Position"; key: "position"; options: [{text: "Bottom", value: "bottom"}, {text: "Left", value: "left"}, {text: "Right", value: "right"}] }
            Choice { label: "Style"; key: "attached"; options: [{text: "Floating", value: "false"}, {text: "Attached", value: "true"}] }
            Choice { label: "Size"; key: "size"; options: [{text: "Small", value: "small"}, {text: "Medium", value: "medium"}, {text: "Large", value: "large"}] }
            Choice { label: "Hide automatically"; key: "autohide"; options: [{text: "Off", value: "false"}, {text: "On", value: "true"}] }
            Choice { label: "Magnify icons under the pointer"; key: "magnify"; options: [{text: "Off", value: "false"}, {text: "On", value: "true"}] }
            Choice { label: "Hide while tiling"; key: "hideInTiling"; options: [{text: "Off", value: "false"}, {text: "On", value: "true"}] }
            Choice { label: "Show on"; key: "outputs"; options: [{text: "Main display", value: "primary"}, {text: "All displays", value: "all"}] }
        }
    }
}
