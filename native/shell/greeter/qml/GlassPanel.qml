// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Greeter

// Frosted glass: a crop of the (already blurred) wallpaper under a translucent
// tint, a hairline stroke and a thin highlight on the top edge. `blurred` is
// the full-screen blurred wallpaper item owned by Main.qml.
Item {
    id: gp
    property Item blurred: null
    property real radius: height / 2
    property bool shadow: true
    property alias tintOpacity: tint.opacity

    ShaderEffectSource {
        id: crop
        visible: false
        sourceItem: gp.blurred
        sourceRect: gp.blurred ? gp.mapToItem(gp.blurred, 0, 0, gp.width, gp.height) : Qt.rect(0, 0, 1, 1)
        textureSize: Qt.size(Math.max(1, gp.width), Math.max(1, gp.height))
        live: true
    }
    Item {
        id: maskItem
        visible: false
        layer.enabled: true
        width: gp.width; height: gp.height
        Rectangle { anchors.fill: parent; radius: gp.radius; color: "black" }
    }
    MultiEffect {
        id: fx
        anchors.fill: parent
        source: crop
        maskEnabled: true
        maskSource: maskItem
        shadowEnabled: gp.shadow
        shadowColor: "black"
        shadowOpacity: Style.dark ? 0.38 : 0.26
        shadowBlur: 1.0
        shadowVerticalOffset: Math.max(4, gp.height * 0.12)
        autoPaddingEnabled: true
    }
    Rectangle {
        id: tint
        anchors.fill: parent
        radius: gp.radius
        color: Style.glassTint
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        // Lit from above: the veil is strongest at the top edge and fades to 40%.
        gradient: Gradient {
            GradientStop { position: 0.0; color: Style.glassVeil }
            GradientStop { position: 1.0; color: Qt.rgba(Style.glassVeil.r, Style.glassVeil.g, Style.glassVeil.b, Style.glassVeil.a * 0.4) }
        }
    }
    // Top-edge highlight: a 1 px line that fades towards both ends.
    Rectangle {
        height: 1
        y: 1
        x: gp.radius * 0.6
        width: Math.max(0, gp.width - gp.radius * 1.2)
        visible: width > 8
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
