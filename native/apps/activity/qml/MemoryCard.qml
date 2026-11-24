import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var mem: eng.activity.memory || ({})
    readonly property var zram: eng.activity.zram || []
    title: qsTr("Memory")
    summary: Fmt.mb(mem.used_mb || 0) + " / " + Fmt.mb(mem.total_mb || 0)
    Hero {
        big: Math.round(c.mem.percent || 0) + "%"
        caption: qsTr("RAM in use")
        graph.values: c.eng.history.mem || []
        graph.title: qsTr("Memory use")
        graph.color: Pal.series1
    }
    LoadBar { Layout.fillWidth: true; label: qsTr("RAM"); value: c.mem.percent || 0 }
    Txt { text: Fmt.mb(c.mem.used_mb || 0) + " / " + Fmt.mb(c.mem.total_mb || 0); font.pixelSize: 11; color: TaosStyle.textTertiary }
    ColumnLayout {
        visible: (c.mem.swap_total_mb || 0) > 0
        Layout.fillWidth: true
        spacing: 4
        LoadBar { Layout.fillWidth: true; label: qsTr("Swap"); value: c.mem.swap_percent || 0 }
        Txt { text: Fmt.mb(c.mem.swap_used_mb || 0) + " / " + Fmt.mb(c.mem.swap_total_mb || 0); font.pixelSize: 11; color: TaosStyle.textTertiary }
    }
    ColumnLayout {
        visible: c.zram.length > 0
        Layout.fillWidth: true
        spacing: 3
        Rectangle { Layout.fillWidth: true; height: 1; color: Pal.hairline }
        Txt { text: qsTr("ZRAM compression"); font.pixelSize: 11; color: TaosStyle.textTertiary }
        Repeater {
            model: c.zram
            RowLayout {
                required property var modelData
                Layout.fillWidth: true
                Txt { text: modelData.device; font.pixelSize: 11; color: TaosStyle.textSecondary; Layout.fillWidth: true }
                Txt {
                    text: Fmt.mb(modelData.compr_mb) + " ← " + Fmt.mb(modelData.orig_mb) + " (" + Number(modelData.ratio).toFixed(1) + "×)"
                    font.pixelSize: 11; color: TaosStyle.textTertiary
                }
            }
        }
    }
}
