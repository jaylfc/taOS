import QtQuick
import QtQuick.Layouts
import Taos.Style 1.0
import Taos.Activity 1.0

Card {
    id: c
    property var eng
    readonly property var models: eng.loadedModels
    title: qsTr("Loaded Models (%1)").arg(models.length)
    Txt {
        visible: c.models.length === 0
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
        elide: Text.ElideNone
        font.pixelSize: 12
        font.italic: true
        color: TaosStyle.textTertiary
        text: qsTr("No models currently loaded \u2014 backends report here when they hold a model in memory, on this host or any cluster worker.")
    }
    ColumnLayout {
        Layout.fillWidth: true
        spacing: 6
        Accessible.role: Accessible.List
        Accessible.name: qsTr("Loaded models")
        Repeater {
            model: c.models
            Rectangle {
                id: row
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: rl.implicitHeight + 16
                radius: Pal.innerRadius
                color: Pal.inner
                Accessible.role: Accessible.ListItem
                Accessible.name: modelData.name + (modelData.host && modelData.host !== "controller" ? ", on " + modelData.host : "")
                RowLayout {
                    id: rl
                    anchors { left: parent.left; right: parent.right; verticalCenter: parent.verticalCenter; margins: 10 }
                    spacing: 10
                    Rectangle {
                        width: 8; height: 8; radius: 4; color: Pal.live
                        SequentialAnimation on opacity {
                            running: !TaosStyle.reduceMotion && c.visible
                            loops: Animation.Infinite
                            NumberAnimation { to: 0.35; duration: 1100; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 1; duration: 1100; easing.type: Easing.InOutSine }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 2
                        RowLayout {
                            spacing: 8
                            Txt { text: row.modelData.name; font.weight: Font.Medium; Layout.fillWidth: true }
                            Chip {
                                visible: !!row.modelData.host && row.modelData.host !== "controller"
                                text: row.modelData.host || ""
                                tone: Pal.neutral
                            }
                        }
                        Txt {
                            Layout.fillWidth: true
                            font.pixelSize: 11
                            color: TaosStyle.textTertiary
                            text: {
                                var p = []
                                if (row.modelData.purpose) p.push(row.modelData.purpose)
                                p.push(row.modelData.backend)
                                if (row.modelData.ram_mb > 0) p.push(Fmt.mb(row.modelData.ram_mb))
                                if (row.modelData.vram_mb > 0) p.push("VRAM " + Fmt.mb(row.modelData.vram_mb))
                                return p.join(" · ")
                            }
                        }
                    }
                }
            }
        }
    }
}
