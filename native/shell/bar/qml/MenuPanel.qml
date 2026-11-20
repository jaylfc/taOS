import QtQuick
import QtQuick.Effects
import Taos.Bar

// The frosted panel, same recipe as the login screen's glass: a tint, a veil
// that is strongest at the top edge, a one pixel highlight and a hairline.
// The compositor does not blur behind layer surfaces yet, so the tint is
// denser than the greeter's; if it ever blurs, lower `tintAlpha`.
Item {
    id: gp
    property real radius: 14
    property real tintAlpha: Theme.variant === "light" ? 0.90 : 0.86
    readonly property bool dark: Theme.variant !== "light"

    // Soft drop shadow, painted from a rounded rect so it follows the corners.
    Rectangle {
        id: shadowSource
        anchors.fill: parent
        radius: gp.radius
        color: Theme.ground  // same colour as the tint's base, so only the shadow shows beyond the edge
        visible: false
        layer.enabled: true
    }
    MultiEffect {
        anchors.fill: shadowSource
        source: shadowSource
        autoPaddingEnabled: true
        shadowEnabled: true
        shadowColor: "black"
        shadowOpacity: gp.dark ? 0.45 : 0.28
        shadowBlur: 1.0
        shadowVerticalOffset: 10
        opacity: 1
        // Only the shadow is wanted; the source rect is covered by the tint below.
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        color: Qt.rgba(Theme.ground.r, Theme.ground.g, Theme.ground.b, gp.tintAlpha)
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        gradient: Gradient {
            GradientStop { position: 0.0; color: gp.dark ? "#1CFFFFFF" : "#00FFFFFF" }
            GradientStop { position: 1.0; color: gp.dark ? "#06FFFFFF" : "#00FFFFFF" }
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
            GradientStop { position: 0.5; color: gp.dark ? "#38FFFFFF" : "#CCFFFFFF" }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: gp.radius
        color: "transparent"
        border.width: 1
        border.color: Qt.rgba(Theme.text.r, Theme.text.g, Theme.text.b, gp.dark ? 0.24 : 0.30)
    }
}
