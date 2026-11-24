import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// One card per GPU the controller reports.
Card {
    id: c
    property var eng
    property var gpu          // normalised entry from the engine (index, label, loadPercent, vram...)
    property int total: 1
    readonly property int idx: gpu ? gpu.index : 0
    readonly property bool hasLoad: gpu && gpu.loadPercent >= 0
    readonly property bool hasVram: gpu && gpu.vramPercent >= 0
    title: total > 1 ? qsTr("GPU %1").arg(idx + 1) : qsTr("GPU")
    summary: gpu ? gpu.label : ""
    Hero {
        visible: c.hasLoad
        big: Math.round(c.gpu ? c.gpu.loadPercent : 0) + "%"
        caption: c.gpu && c.gpu.freqHz > 0 ? qsTr("load · %1 GHz").arg((c.gpu.freqHz / 1e9).toFixed(2)) : qsTr("load")
        graph.values: c.eng.history["gpu." + c.idx + ".load"] || []
        graph.title: qsTr("GPU %1 load").arg(c.idx + 1)
        graph.color: Pal.series1
    }
    Hero {
        visible: c.hasVram
        big: Math.round(c.gpu ? c.gpu.vramPercent : 0) + "%"
        caption: c.gpu ? qsTr("VRAM %1 of %2").arg(Fmt.mb(c.gpu.vramUsedMb)).arg(Fmt.mb(c.gpu.vramTotalMb)) : ""
        graph.values: c.eng.history["gpu." + c.idx + ".vram"] || []
        graph.title: qsTr("GPU %1 VRAM").arg(c.idx + 1)
        graph.color: Pal.series1
    }
    Txt {
        visible: !c.hasLoad && !c.hasVram
        text: qsTr("Stats unavailable")
        font.pixelSize: 12
        color: TaosStyle.textTertiary
    }
}
