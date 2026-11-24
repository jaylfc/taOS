// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls
import QtQuick.Controls
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

// The single scrolling dashboard: a responsive grid on desktop, one column of
// collapsible cards on a phone.
Flickable {
    id: flick
    property var eng
    property var feed
    property bool phone: false
    signal openProcesses()
    signal openModelActivity()

    contentWidth: width
    contentHeight: grid.implicitHeight + 2 * margin
    readonly property int margin: phone ? 12 : 20
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    ScrollBar.vertical: ScrollBar { }

    GridLayout {
        id: grid
        x: flick.margin; y: flick.margin
        width: flick.width - 2 * flick.margin
        columns: flick.phone ? 1 : (width >= 1080 ? 3 : (width >= 700 ? 2 : 1))
        columnSpacing: Pal.gap
        rowSpacing: Pal.gap
        function span(n) { return Math.min(n, columns) }

        CpuCard { eng: flick.eng; collapsible: flick.phone; open: true; Layout.columnSpan: grid.span(2) }
        MemoryCard { eng: flick.eng; collapsible: flick.phone }
        Repeater {
            model: flick.eng.gpus
            GpuCard {
                required property var modelData
                eng: flick.eng; gpu: modelData; total: flick.eng.gpus.length; collapsible: flick.phone
            }
        }
        NpuCard {
            eng: flick.eng; collapsible: flick.phone
            visible: !!flick.eng.activity.npu && !!flick.eng.activity.npu.type && flick.eng.activity.npu.type !== "none"
        }
        DiskCard { eng: flick.eng; collapsible: flick.phone }
        NetworkCard {
            eng: flick.eng; collapsible: flick.phone
            visible: (flick.eng.activity.network || []).length > 0
        }
        ThermalCard {
            eng: flick.eng; collapsible: flick.phone
            visible: (flick.eng.activity.thermal || []).length > 0
        }
        ModelsCard { eng: flick.eng; collapsible: flick.phone; open: true; Layout.columnSpan: grid.span(3) }
        ClusterCard { eng: flick.eng; collapsible: flick.phone; Layout.columnSpan: grid.span(3) }
        SchedulerCard {
            eng: flick.eng; collapsible: flick.phone
            visible: flick.eng.hasScheduler
            Layout.columnSpan: grid.span(3)
        }
        // Phone: Model Activity and Top Processes are pushed pages.
        NavRow {
            visible: flick.phone
            title: qsTr("Model Activity")
            summary: flick.feed.live ? qsTr("live") : qsTr("offline")
            onActivated: flick.openModelActivity()
            Layout.columnSpan: grid.span(3)
        }
        ModelActivityCard {
            visible: !flick.phone
            eng: flick.eng; feed: flick.feed
            Layout.columnSpan: grid.span(3)
        }
        NavRow {
            visible: flick.phone
            title: qsTr("Top Processes")
            summary: (flick.eng.activity.processes || []).length
            onActivated: flick.openProcesses()
            Layout.columnSpan: grid.span(3)
        }
        ProcessesCard {
            visible: !flick.phone
            eng: flick.eng
            Layout.columnSpan: grid.span(3)
        }
    }
}
