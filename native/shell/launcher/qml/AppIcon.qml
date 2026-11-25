// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// An app's icon, resolved the way the dock resolves it; a quiet monogram only when the theme has none.
Item {
    id: ai
    property string source: ""
    property bool hasIcon: false
    property string name: ""
    property real size: 40
    width: size; height: size
    Accessible.ignored: true
    Image {
        anchors.fill: parent
        visible: ai.hasIcon
        source: ai.hasIcon ? ai.source : ""
        sourceSize: Qt.size(ai.size * 2, ai.size * 2)
        fillMode: Image.PreserveAspectFit
        smooth: true
        asynchronous: false
    }
    Monogram { anchors.fill: parent; visible: !ai.hasIcon; diameter: ai.size; name: ai.name }
}
