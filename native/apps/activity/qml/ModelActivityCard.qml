import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    property var feed
    title: qsTr("Model Activity")
    trailing: Row {
        spacing: 6
        Rectangle { width: 8; height: 8; radius: 4; anchors.verticalCenter: parent.verticalCenter; color: c.feed.live ? Pal.live : TaosStyle.textTertiary }
        Txt {
            text: c.feed.live ? qsTr("live") : qsTr("offline")
            font.pixelSize: 11; color: TaosStyle.textTertiary
            // State changes only, never per-sample: a polite status for assistive technology.
            Accessible.role: Accessible.StaticText
            Accessible.name: qsTr("Model activity is %1").arg(c.feed.live ? qsTr("live") : qsTr("offline"))
        }
    }
    ModelActivityPanel { feed: c.feed; nowSec: c.eng.now }
}
