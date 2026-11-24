import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0

Card {
    id: c
    property var eng
    title: qsTr("Top Processes")
    ProcessesList { processes: c.eng.activity.processes || [] }
}
