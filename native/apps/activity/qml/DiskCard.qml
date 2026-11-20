import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var disk: eng.activity.disk || ({})
    readonly property var io: disk.io_rate || ({})
    title: qsTr("Disk")
    dot: Pal.warn
    summary: qsTr("%1 / %2 GB").arg(disk.used_gb || 0).arg(disk.total_gb || 0)
    LoadBar { Layout.fillWidth: true; label: qsTr("Used"); value: c.disk.usage_percent || 0 }
    Sparkline {
        Layout.fillWidth: true
        Layout.preferredHeight: 72
        values: c.eng.history.diskRead || []
        values2: c.eng.history.diskWrite || []
        title: qsTr("Disk read"); title2: qsTr("Disk write")
        color: Pal.info; color2: Pal.warn
        maxValue: 0
        format: function (v) { return Fmt.rate(v) }
    }
    RowLayout {
        Layout.fillWidth: true
        Rectangle { width: 8; height: 8; radius: 4; color: Pal.info }
        Txt { text: qsTr("Read"); font.pixelSize: 11; color: TaosStyle.textTertiary }
        Txt { text: Fmt.rate(c.io.read_bps || 0); font.pixelSize: 12; color: TaosStyle.textSecondary; Layout.fillWidth: true }
        Rectangle { width: 8; height: 8; radius: 4; color: Pal.warn }
        Txt { text: qsTr("Write"); font.pixelSize: 11; color: TaosStyle.textTertiary }
        Txt { text: Fmt.rate(c.io.write_bps || 0); font.pixelSize: 12; color: TaosStyle.textSecondary }
    }
}
