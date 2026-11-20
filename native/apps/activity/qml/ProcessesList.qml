import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// The top-processes table (pid, user, name, CPU %, RSS), used inline and on
// the pushed phone page.
ColumnLayout {
    id: t
    property var processes: []
    spacing: 2
    Layout.fillWidth: true
    Accessible.role: Accessible.Table
    Accessible.name: qsTr("Top processes")
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Txt { text: qsTr("PID"); font.pixelSize: 10; color: TaosStyle.textTertiary; Layout.preferredWidth: 68 }
        Txt { text: qsTr("User"); font.pixelSize: 10; color: TaosStyle.textTertiary; Layout.preferredWidth: 72 }
        Txt { text: qsTr("Name"); font.pixelSize: 10; color: TaosStyle.textTertiary; Layout.fillWidth: true }
        Txt { text: qsTr("CPU"); font.pixelSize: 10; color: TaosStyle.textTertiary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 48 }
        Txt { text: qsTr("Memory"); font.pixelSize: 10; color: TaosStyle.textTertiary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 72 }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Pal.hairline }
    Repeater {
        model: t.processes
        RowLayout {
            required property var modelData
            Layout.fillWidth: true
            Layout.preferredHeight: 24
            spacing: 10
            Accessible.role: Accessible.Row
            Accessible.name: qsTr("%1, process %2, %3 percent CPU, %4").arg(modelData.name).arg(modelData.pid).arg(Math.round(modelData.cpu_percent)).arg(Fmt.mb(modelData.rss_mb))
            Txt { text: modelData.pid; font.pixelSize: 12; color: TaosStyle.textTertiary; Layout.preferredWidth: 68 }
            Txt { text: modelData.user; font.pixelSize: 12; color: TaosStyle.textSecondary; Layout.preferredWidth: 72 }
            Txt { text: modelData.name; font.pixelSize: 12; Layout.fillWidth: true }
            Txt { text: Math.round(modelData.cpu_percent) + "%"; font.pixelSize: 12; color: TaosStyle.textSecondary; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 48 }
            Txt { text: Fmt.mb(modelData.rss_mb); font.pixelSize: 12; horizontalAlignment: Text.AlignRight; Layout.preferredWidth: 72 }
        }
    }
}
