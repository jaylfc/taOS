import QtQuick
import Taos.Apps 1.0

// A panel: the theme surface (or glass tint for bars and sheets) and one
// hairline. Elevation is declared once; there is no veil, highlight or shadow.
Rectangle {
    id: root
    property bool strong: false          // bars and sheets use the full glass tint, cards the lighter surface
    property bool edgeTop: true          // kept for callers; no top highlight is drawn any more
    radius: Look.radiusCard
    color: strong ? Look.ground : Look.card
    border.width: 1
    border.color: Look.hairline
}
