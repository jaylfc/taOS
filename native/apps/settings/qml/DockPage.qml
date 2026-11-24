import QtQuick
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "dock"
    readonly property var dock: Settings.dock

    Txt {
        width: parent.width
        visible: !page.dock.dockInstalled
        text: qsTr("The dock is not installed on this device. Your choices are saved and apply when it is.")
        color: TaosStyle.textSecondary
        font.pixelSize: 12
    }
    Txt {
        width: parent.width
        visible: page.dock.errorText.length > 0
        text: page.dock.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }
    Group {
        heading: qsTr("Placement")
        SRow {
            settingId: "dock.position"
            Segmented {
                label: qsTr("Position on screen")
                options: [{ value: "bottom", label: qsTr("Bottom") }, { value: "left", label: qsTr("Left") }, { value: "right", label: qsTr("Right") }]
                current: page.dock.position
                onPicked: (v) => page.dock.set("position", v)
            }
        }
        SRow {
            settingId: "dock.attached"
            Toggle { label: qsTr("Attached to the edge"); checked: page.dock.attached; onFlipped: (on) => page.dock.set("attached", on) }
        }
        SRow {
            settingId: "dock.autohide"
            Toggle { label: qsTr("Hide automatically"); checked: page.dock.autohide; onFlipped: (on) => page.dock.set("autohide", on) }
        }
        SRow {
            settingId: "dock.hideInTiling"
            Toggle { label: qsTr("Hide in tiling mode"); checked: page.dock.hideInTiling; onFlipped: (on) => page.dock.set("hideInTiling", on) }
        }
    }
    Group {
        heading: qsTr("Icons")
        SRow {
            settingId: "dock.size"
            Segmented {
                label: qsTr("Icon size")
                options: [{ value: "small", label: qsTr("Small") }, { value: "medium", label: qsTr("Medium") }, { value: "large", label: qsTr("Large") }]
                current: page.dock.size
                onPicked: (v) => page.dock.set("size", v)
            }
        }
        SRow {
            settingId: "dock.magnify"
            Toggle { label: qsTr("Magnification"); checked: page.dock.magnify; onFlipped: (on) => page.dock.set("magnify", on) }
        }
    }
}
