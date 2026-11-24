import QtQuick
import QtQuick.Layouts
import Taos.Settings 1.0
import Taos.Style 1.0

PageBase {
    id: page
    sectionId: "display"
    readonly property var disp: Settings.display
    readonly property var ctl: Settings.display.controller
    phase: disp.state
    message: disp.message
    onRetry: disp.reload()

    // "Keep this setting?" for a refresh rate change: the engine reverts by itself when the time is up.
    property int secondsLeft: ctl.trialSecondsLeft
    Connections { target: page.ctl; function onTrialChanged() { page.secondsLeft = page.ctl.trialSecondsLeft } }
    Timer { running: page.ctl.trialActive; interval: 500; repeat: true; onTriggered: page.secondsLeft = page.ctl.trialSecondsLeft }

    Txt {
        width: parent.width
        visible: page.ctl.errorText.length > 0
        text: page.ctl.errorText
        color: TaosStyle.danger
        Accessible.role: Accessible.AlertMessage
    }

    Rectangle {
        id: keep
        width: parent.width
        visible: page.ctl.trialActive
        height: visible ? keepRow.implicitHeight + 28 : 0
        radius: 10
        color: TaosStyle.raised
        border.width: 1
        border.color: TaosStyle.accent
        Accessible.role: Accessible.AlertMessage
        Accessible.name: qsTr("Keep this setting? %1. Going back in %2 seconds.").arg(page.ctl.trialText).arg(page.secondsLeft)
        onVisibleChanged: if (visible) Settings.announce(page, Accessible.name)
        RowLayout {
            id: keepRow
            anchors { fill: parent; margins: 14 }
            spacing: 12
            ColumnLayout {
                Layout.fillWidth: true
                Txt { text: qsTr("Keep this setting?"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                Txt { text: qsTr("%1. Going back in %2 s.").arg(page.ctl.trialText).arg(page.secondsLeft); color: TaosStyle.textSecondary; font.pixelSize: 12; Layout.fillWidth: true }
            }
            Btn { text: qsTr("Go back"); onClicked: page.ctl.revert() }
            Btn { id: keepBtn; text: qsTr("Keep"); kind: "accent"; onClicked: page.ctl.confirm() }
        }
    }

    Repeater {
        model: page.ctl.outputs
        delegate: Group {
            id: screen
            required property var modelData
            required property int index
            heading: page.ctl.outputs.length > 1 ? modelData.label : qsTr("Screen")
            footnote: modelData.isRecommended ? qsTr("These are the recommended settings for this screen.") : ""
            SRow {
                settingId: "display.screen"; linkable: screen.index === 0
                description: modelData.nativeText.length ? qsTr("%1 on %2. The screen's own size is %3.").arg(modelData.modeText).arg(modelData.label).arg(modelData.nativeText) : modelData.modeText
                Txt { text: modelData.modeText; color: TaosStyle.textSecondary; wrapMode: Text.NoWrap }
            }
            SRow {
                settingId: "display.refresh"; linkable: screen.index === 0
                unavailable: modelData.rates.length < 2 || page.ctl.trialActive
                Picker {
                    label: qsTr("Refresh rate")
                    model: modelData.rates
                    textRole: "text"
                    currentIndex: { for (let i = 0; i < modelData.rates.length; ++i) if (modelData.rates[i].current) return i; return -1 }
                    onActivated: (i) => page.ctl.setRefresh(modelData.name, modelData.rates[i].mHz)
                }
            }
            SRow {
                settingId: "display.scale"; linkable: screen.index === 0
                Picker {
                    label: qsTr("Size of text and icons")
                    model: modelData.scales
                    currentIndex: modelData.scales.indexOf(modelData.scalePercent)
                    displayText: qsTr("%1%").arg(modelData.scalePercent)
                    onActivated: (i) => page.ctl.setScale(modelData.name, modelData.scales[i] / 100)
                }
            }
            SRow {
                settingId: "display.recommended"; linkable: screen.index === 0
                description: modelData.recommendedText
                unavailable: modelData.isRecommended || page.ctl.trialActive
                Btn { text: qsTr("Use recommended"); onClicked: page.ctl.useRecommended(modelData.name) }
            }
        }
    }
}
