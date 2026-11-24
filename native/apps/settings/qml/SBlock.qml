import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

// A setting whose control is wide (the theme picker, a list): the label on top, the content below.
Item {
    id: blk
    property string settingId: ""
    property string title: settingId.length ? Settings.catalog.title(settingId) : ""
    property string description: settingId.length ? Settings.catalog.description(settingId) : ""
    default property alias content: slot.data
    readonly property bool targeted: settingId.length > 0 && Settings.nav.setting === settingId
    width: parent ? parent.width : 400
    implicitHeight: textCol.implicitHeight + slot.implicitHeight + 36
    Accessible.role: Accessible.Grouping
    Accessible.name: title

    Rectangle { anchors.fill: parent; color: TaosStyle.selection; opacity: blk.targeted ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: TaosStyle.reduceMotion ? 0 : 200 } } }
    Rectangle { anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 } height: 1; color: TaosStyle.hairline }
    ColumnLayout {
        id: textCol
        x: 16; y: 12
        width: parent.width - 32
        spacing: 2
        Txt { Layout.fillWidth: true; text: blk.title; Accessible.ignored: true }
        Txt { Layout.fillWidth: true; visible: text.length > 0; text: blk.description; font.pixelSize: 12; color: TaosStyle.textSecondary; Accessible.ignored: true }
    }
    Item {
        id: slot
        x: 16
        y: textCol.y + textCol.implicitHeight + 12
        width: parent.width - 32
        implicitHeight: childrenRect.height
    }
}
