import QtQuick
import Taos.Overview

// The app's icon (from its .desktop file and the icon theme) or, failing that, a monogram tile.
Item {
    id: badge
    property string iconSource: ""
    property string appName: ""
    property real size: 40
    width: size; height: size
    Accessible.ignored: true
    Image {
        id: img
        anchors.fill: parent
        source: badge.iconSource
        sourceSize: Qt.size(badge.size * 2, badge.size * 2)
        fillMode: Image.PreserveAspectFit
        asynchronous: false
        smooth: true
        visible: status === Image.Ready
    }
    Monogram {
        anchors.fill: parent
        visible: img.status !== Image.Ready
        diameter: badge.size
        name: badge.appName
    }
}
