import QtQuick
import Taos.Apps 1.0

// A frosted panel as the shell draws one: the theme's glass tint, a faint light
// veil, a hairline and a brighter hairline on the top edge. The blur itself
// comes from the compositor behind the window; here the tint is what shows.
Rectangle {
    id: root
    property bool strong: false          // bars and sheets use the full glass tint, cards the lighter surface
    property bool edgeTop: true
    radius: Look.radiusCard
    color: strong ? Look.glassTint : Look.card
    border.width: 1
    border.color: strong ? Look.glassStroke : Look.hairline
    Rectangle {                           // veil
        anchors.fill: parent
        anchors.margins: 1
        radius: root.radius - 1
        color: "transparent"
        gradient: Gradient {
            GradientStop { position: 0; color: Look.glassVeil }
            GradientStop { position: 0.5; color: "transparent" }
        }
        opacity: root.strong ? 1 : 0.55
    }
    Rectangle {                           // top highlight
        visible: root.edgeTop
        x: root.radius; y: 1; width: parent.width - root.radius * 2; height: 1
        color: Look.tint(Look.text, Look.dark ? 0.10 : 0.35)
    }
}
