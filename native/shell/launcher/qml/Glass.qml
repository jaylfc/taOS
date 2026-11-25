// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Effects
import Taos.Launcher

// The bar menus' frosted panel (bar/qml/MenuPanel.qml) on Style tokens: a dense tint (the compositor
// may or may not blur behind layer surfaces; lower tintAlpha if it always does), a veil lit from the
// top, a 1 px highlight, a hairline and a soft drop shadow that follows the corners.
Item {
    id: gp
    property real radius: Math.round((22) * Style.radiusScale)
    property string kind: Style.dockSurface        // translucent (frosted glass, the default) | solid | raised (bevelled)
    property real tintAlpha: kind === "translucent" ? (Style.dark ? 0.86 : 0.90) : 1.0
    property bool frame: true                        // false: a full screen surface has no edge and no shadow
    readonly property int bw: frame ? Style.borderWidth : 0
    readonly property bool ring: Style.launcherPlace === "ring"       // the command ring: an accent outline with a soft glow

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
        shadowEnabled: gp.frame && Style.shadowStrength > 0
        shadowColor: gp.ring ? Style.accent : "black"
        shadowOpacity: gp.ring ? 0.28 : Math.min(1, (Style.dark ? 0.45 : 0.28) * Style.shadowStrength)
        shadowBlur: 1.0
        shadowVerticalOffset: 10
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        color: gp.kind === "raised" ? Style.surface : Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, gp.tintAlpha)
    }
    Rectangle {
        visible: gp.kind === "translucent"
        anchors.fill: parent
        radius: gp.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: Style.glassVeil }
            GradientStop { position: 1.0; color: Qt.rgba(Style.glassVeil.r, Style.glassVeil.g, Style.glassVeil.b, Style.glassVeil.a * 0.22) }
        }
    }
    Rectangle {
        visible: gp.kind === "translucent"
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
        border.width: gp.bw
        border.color: gp.ring ? Style.accent : gp.kind === "raised" ? Style.bevelDark : Style.glassStroke
    }
    // raised: the lit top and left edges inside the dark outline
    Rectangle { visible: gp.kind === "raised" && gp.bw > 0; x: gp.bw; y: gp.bw; width: parent.width - 2 * gp.bw; height: gp.bw; color: Style.bevelLight }
    Rectangle { visible: gp.kind === "raised" && gp.bw > 0; x: gp.bw; y: gp.bw; width: gp.bw; height: parent.height - 2 * gp.bw; color: Style.bevelLight }
    // the faint line texture of themes that ask for it
    Item {
        visible: Style.scanlines
        anchors.fill: parent
        clip: true
        Repeater {
            model: Style.scanlines ? Math.ceil(gp.height / 3) : 0
            Rectangle { y: index * 3; width: gp.width; height: 1; color: Style.text; opacity: 0.03 }
        }
    }
}
