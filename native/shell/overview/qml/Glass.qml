import QtQuick
import QtQuick.Effects
import Taos.Overview

// The greeter's glass without a wallpaper to crop: a translucent tint over whatever the compositor
// shows behind this layer surface (blurred by the compositor when `layer_effects` is set for the
// "taos-overview" namespace), a veil lit from above, a 1 px top highlight and hairline stroke.
Item {
    id: gp
    property real radius: 22
    property bool shadow: false   // a shadow's translucent pixels would be blurred too, as a halo
    property real tintBoost: 0.0   // extra tint opacity for places where the backdrop is busy
    Item {
        id: body
        anchors.fill: parent
        Rectangle {
            id: base
            anchors.fill: parent
            radius: gp.radius
            color: Qt.rgba(Style.glassTint.r, Style.glassTint.g, Style.glassTint.b,
                           Math.min(0.94, Style.glassTint.a + 0.14 + gp.tintBoost))
        }
        Rectangle {
            anchors.fill: parent
            radius: gp.radius
            gradient: Gradient {
                GradientStop { position: 0.0; color: Style.glassVeil }
                GradientStop { position: 1.0; color: Qt.rgba(Style.glassVeil.r, Style.glassVeil.g, Style.glassVeil.b, Style.glassVeil.a * 0.4) }
            }
        }
        Rectangle {
            height: 1
            y: 1
            x: gp.radius * 0.6
            width: Math.max(0, gp.width - gp.radius * 1.2)
            visible: width > 8
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "transparent" }
                GradientStop { position: 0.5; color: Style.dark ? "#38FFFFFF" : "#CCFFFFFF" }
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
        layer.enabled: gp.shadow
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "black"
            shadowOpacity: Style.dark ? 0.42 : 0.28
            shadowBlur: 1.0
            shadowVerticalOffset: Math.max(6, gp.height * 0.10)
            autoPaddingEnabled: true
        }
    }
}
