import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var npu: eng.activity.npu || ({})
    readonly property var cores: npu.cores || []
    title: qsTr("NPU")
    summary: (npu.type || qsTr("Unknown")) + (npu.tops ? " · " + npu.tops + " TOPS" : "")
             + (npu.freq_hz ? " · " + (npu.freq_hz / 1e9).toFixed(2) + " GHz" : "")
    Hero {
        visible: c.cores.length > 0
        big: Math.round((c.eng.history.npu || [0]).slice(-1)[0]) + "%"
        caption: qsTr("average core load")
        graph.values: c.eng.history.npu || []
        graph.title: qsTr("NPU load")
        graph.color: Pal.series1
    }
    ColumnLayout {
        visible: c.cores.length > 0
        Layout.fillWidth: true
        spacing: 6
        Repeater {
            model: c.cores
            LoadBar {
                required property var modelData
                Layout.fillWidth: true
                label: qsTr("Core %1").arg(modelData.core)
                value: modelData.load_percent || 0
            }
        }
    }
    Txt {
        visible: c.cores.length === 0
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        elide: Text.ElideNone
        font.pixelSize: 12
        color: TaosStyle.textTertiary
        text: qsTr("Per-core stats require debugfs access. Run as root or grant cap_dac_read_search.")
    }
}
