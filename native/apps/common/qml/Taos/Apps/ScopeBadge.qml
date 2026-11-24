import QtQuick
import Taos.Apps 1.0

// One scope. `kind` is "normal", "dropped" (requested but not granted) or
// "added" (granted beyond the request): a narrowing or a widening is never silent.
Chip {
    id: root
    property string scope: ""
    property string kind: "normal"
    text: scope
    mono: true
    tone: kind === "dropped" ? "danger" : kind === "added" ? "warning" : "neutral"
    accessibleName: kind === "dropped" ? scope + " (dropped from request)"
                  : kind === "added" ? scope + " (granted beyond request)" : scope
}
