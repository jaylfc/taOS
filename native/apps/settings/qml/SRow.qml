import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

// One setting: a label, an optional one line description, the control on the right. Title and description come from
// the catalog (the same words the search finds), so a row only names its setting id.
Item {
    id: row
    property string settingId: ""
    property bool linkable: true            // false for the second monitor's rows: only one row answers a deep link
    property string title: settingId.length ? Settings.catalog.title(settingId) : ""
    property string description: settingId.length ? Settings.catalog.description(settingId) : ""
    property string note: ""                // replaces the description, e.g. "Coming soon"
    property bool unavailable: false        // dims the row and disables its control
    default property alias control: slot.data
    readonly property bool targeted: linkable && settingId.length > 0 && Settings.nav.setting === settingId

    width: parent ? parent.width : 400
    implicitHeight: Math.max(52, textCol.implicitHeight + 22)
    Accessible.role: Accessible.Grouping
    Accessible.name: title
    Accessible.description: note.length ? note : description

    Rectangle {
        anchors.fill: parent
        color: TaosStyle.selection
        opacity: row.targeted ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: TaosStyle.reduceMotion ? 0 : 200 } }
    }
    Rectangle {
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; leftMargin: 16 }
        height: 1
        color: TaosStyle.hairline
    }
    RowLayout {
        anchors { fill: parent; leftMargin: 16; rightMargin: 16 }
        spacing: 16
        ColumnLayout {
            id: textCol
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 2
            Txt {
                Layout.fillWidth: true
                text: row.title
                color: row.unavailable ? TaosStyle.textTertiary : TaosStyle.text
                Accessible.ignored: true
            }
            Txt {
                Layout.fillWidth: true
                visible: text.length > 0
                text: row.note.length ? row.note : row.description
                font.pixelSize: 12
                color: TaosStyle.textSecondary
                Accessible.ignored: true
            }
        }
        Row {
            id: slot
            Layout.alignment: Qt.AlignVCenter
            spacing: 8
            enabled: !row.unavailable
            opacity: row.unavailable ? 0.5 : 1
        }
    }
}
