import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0

// A big number with a caption on the left and a live graph filling the rest.
RowLayout {
    id: hero
    property string big: ""
    property string caption: ""
    property alias graph: g
    spacing: 14
    Layout.fillWidth: true
    ColumnLayout {
        Layout.preferredWidth: 104
        Layout.alignment: Qt.AlignTop
        spacing: 2
        Txt {
            text: hero.big
            font.pixelSize: 28
            font.weight: Font.DemiBold
            Layout.fillWidth: true
            Accessible.ignored: true
        }
        Txt {
            text: hero.caption
            font.pixelSize: 11
            color: TaosStyle.textTertiary
            wrapMode: Text.WordWrap
            maximumLineCount: 3
            Layout.fillWidth: true
        }
    }
    Sparkline { id: g; Layout.fillWidth: true; Layout.preferredHeight: 84 }
}
