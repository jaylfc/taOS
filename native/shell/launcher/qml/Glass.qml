// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Launcher

// The bar menus' frosted panel (bar/qml/MenuPanel.qml) on Style tokens: a dense tint (the compositor
// may or may not blur behind layer surfaces; lower tintAlpha if it always does), a veil lit from the
// top, a 1 px highlight, a hairline and a soft drop shadow that follows the corners.
Item {
    id: gp
    property real radius: 22
    property real tintAlpha: Style.dark ? 0.86 : 0.90

    Rectangle {
        id: shadowSource
        anchors.fill: parent
        radius: gp.radius
        color: Style.ground
        visible: false
        layer.enabled: true
    }
    MultiEffect {
        anchors.fill: shadowSource
        source: shadowSource
        autoPaddingEnabled: true
        shadowEnabled: true
        shadowColor: "black"
        shadowOpacity: Style.dark ? 0.45 : 0.28
        shadowBlur: 1.0
        shadowVerticalOffset: 10
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        color: Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, gp.tintAlpha)
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Style.glassVeil }
            GradientStop { position: 1.0; color: Qt.rgba(Style.glassVeil.r, Style.glassVeil.g, Style.glassVeil.b, Style.glassVeil.a * 0.22) }
        }
    }
    Rectangle {
        height: 1
        y: 1
        x: gp.radius * 0.6
        width: Math.max(0, gp.width - gp.radius * 1.2)
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.5; color: Style.glassHighlight }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        color: "transparent"
        border.width: 1
        border.color: Style.glassStroke
    }
}
