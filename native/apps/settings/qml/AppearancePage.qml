import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "appearance"
    readonly property var themes: Settings.themes

    Group {
        heading: qsTr("Colours")
        SRow {
            settingId: "appearance.mode"
            Segmented {
                label: qsTr("Appearance")
                options: [{ value: "dark", label: qsTr("Dark") }, { value: "light", label: qsTr("Light") }, { value: "auto", label: qsTr("Auto") }]
                current: page.themes.mode
                onPicked: (v) => {
                    if (page.themes.setMode(v)) Settings.announce(page, qsTr("Appearance: %1").arg(v))
                }
            }
        }
        SBlock {
            settingId: "appearance.theme"
            Flow {
                id: flow
                width: parent.width
                spacing: 12
                Repeater {
                    model: page.themes.themes
                    delegate: FocusScope {
                        id: card
                        required property var modelData
                        width: 132; height: 108
                        activeFocusOnTab: true
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: modelData.name
                        Accessible.description: modelData.description
                        Accessible.checked: modelData.current
                        Keys.onReturnPressed: pick()
                        Keys.onSpacePressed: pick()
                        function pick() {
                            if (page.themes.setTheme(modelData.id)) Settings.announce(page, qsTr("Theme: %1").arg(modelData.name))
                        }
                        // The preview paints the theme's own swatches (ground, surface, accent), not the live tokens.
                        Rectangle {
                            id: preview
                            width: parent.width; height: 72
                            radius: 9
                            color: modelData.swatches[0]
                            border.width: modelData.current ? 2 : 1
                            border.color: modelData.current ? TaosStyle.accent : TaosStyle.hairline
                            Rectangle { x: 10; y: 10; width: 62; height: 52; radius: 6; color: modelData.swatches[1] }
                            Rectangle { x: 80; y: 14; width: 40; height: 8; radius: 4; color: modelData.swatches[2] }
                            Rectangle { x: 80; y: 28; width: 28; height: 6; radius: 3; color: modelData.swatches[1] }
                            Rectangle {
                                anchors.fill: parent; anchors.margins: -3
                                radius: preview.radius + 3; color: "transparent"
                                border.width: card.activeFocus ? 2 : 0; border.color: TaosStyle.focusRing
                            }
                            MouseArea { anchors.fill: parent; onClicked: { card.forceActiveFocus(); card.pick() } }
                        }
                        Txt {
                            y: 80; width: parent.width
                            text: modelData.name
                            font.pixelSize: 12
                            font.weight: modelData.current ? Font.DemiBold : Font.Normal
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight; wrapMode: Text.NoWrap
                        }
                    }
                }
            }
        }
    }

    Group {
        heading: qsTr("Windows and motion")
        SRow {
            settingId: "appearance.windowButtons"
            Segmented {
                label: qsTr("Window buttons")
                options: [{ value: "left", label: qsTr("Left") }, { value: "right", label: qsTr("Right") }, { value: "theme", label: qsTr("Follow theme") }]
                current: page.themes.windowButtonsChoice
                onPicked: (v) => {
                    if (page.themes.setWindowButtons(v)) Settings.announce(page, qsTr("Window buttons: %1").arg(v))
                }
            }
        }
        SRow {
            settingId: "appearance.reduceMotion"
            unavailable: true
            note: qsTr("Coming soon. It is not stored per person yet.")
            Toggle { label: qsTr("Reduce motion") }
        }
    }
}
