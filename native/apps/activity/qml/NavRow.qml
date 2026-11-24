import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0

// A phone row that pushes a detail page (Top Processes, Model Activity).
Item {
    id: row
    property string title: ""
    property string summary: ""
    signal activated()
    Layout.fillWidth: true
    implicitHeight: 52
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: title
    Accessible.description: summary
    Accessible.onPressAction: activated()
    Keys.onReturnPressed: activated()
    Keys.onSpacePressed: activated()
    Glass { anchors.fill: parent; color: ma.pressed ? Pal.glassFillHover : Pal.glassFill }
    Rectangle { anchors.fill: parent; radius: Pal.radius; color: "transparent"; border.width: 2; border.color: Pal.focusRing; visible: row.activeFocus }
    RowLayout {
        anchors { fill: parent; leftMargin: Pal.pad; rightMargin: Pal.pad }
        Txt { text: row.title; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
        Txt { text: row.summary; font.pixelSize: 12; color: TaosStyle.textSecondary }
        Txt { text: "›"; font.pixelSize: 15; color: TaosStyle.textTertiary }
    }
    MouseArea { id: ma; anchors.fill: parent; onClicked: row.activated() }
}
