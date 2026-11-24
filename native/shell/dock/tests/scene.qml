// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock
import "../qml"

// Dev scene for dockshot: a stand-in wallpaper, the dock where its surface would be, and optionally an overlay.
Item {
    id: scene
    property string overlay: ""
    property real sx: 0
    property real sy: 0
    property real sw: 100
    property real sh: 100
    property real ax: 0
    property real ay: 0
    Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.dark ? "#33425e" : "#9fb4d6" }
            GradientStop { position: 1.0; color: Theme.dark ? "#1b2233" : "#d8e2f2" }
        }
    }
    // two stand-in windows
    Rectangle { x: 120; y: 90; width: 560; height: 340; radius: 10; color: Theme.surface; border.width: 1; border.color: Theme.hairline }
    Rectangle { x: 360; y: 200; width: 520; height: 320; radius: 10; color: Theme.raised; border.width: 1; border.color: Theme.hairline }
    Dock { x: scene.sx; y: scene.sy; width: scene.sw; height: scene.sh }
    Loader {
        anchors.fill: parent
        source: scene.overlay === "menu" ? "../qml/DockMenu.qml" : scene.overlay === "settings" ? "../qml/DockSettings.qml" : ""
        onLoaded: { item.anchorX = Qt.binding(() => scene.ax); item.anchorY = Qt.binding(() => scene.ay); item.edge = Dock.position }
    }
}
