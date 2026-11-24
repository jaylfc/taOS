// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "desktop"
    readonly property var desk: Settings.desktop

    Txt {
        width: parent.width
        visible: page.desk.errorText.length > 0
        text: page.desk.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }
    Group {
        heading: qsTr("Windows")
        footnote: qsTr("Applies to new windows and to workspaces that have no choice of their own. Change one workspace with Super + Shift + T.")
        SRow {
            settingId: "desktop.mode"
            Segmented {
                label: qsTr("Window style")
                options: [{ value: "desktop", label: qsTr("Desktop") }, { value: "tiling", label: qsTr("Tiling") }]
                current: page.desk.defaultMode
                onPicked: (v) => page.desk.setDefaultMode(v)
            }
        }
        Repeater {
            model: page.desk.workspaces
            delegate: SRow {
                required property var modelData
                title: qsTr("Workspace %1").arg(modelData.name)
                description: modelData.mode === "tiling" ? qsTr("Tiling, its own choice") : qsTr("Desktop, its own choice")
                Btn { text: qsTr("Use the default"); onClicked: page.desk.useDefaultFor(modelData.name) }
            }
        }
    }
    Group {
        id: keys
        heading: qsTr("Keyboard shortcuts")
        footnote: qsTr("These are fixed for now.")
        Repeater {
            model: Settings.shortcuts
            delegate: SRow {
                required property var modelData
                title: modelData.action
                Txt {
                    text: modelData.keys
                    color: TaosStyle.textSecondary
                    wrapMode: Text.NoWrap
                    Accessible.name: modelData.keys
                }
            }
        }
    }
}
