// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Kit: grab-and-go templates, each checked against what this machine has. A check is ready, missing or unknown;
// something the engine cannot detect is shown as unknown, never as missing.
Item {
    id: page
    objectName: "devicesView"
    property bool narrow: false
    readonly property var kits: Survival.kits
    function focusSearch() {}
    Component.onCompleted: { Survival.radios.scan(); kits.refresh() }
    onVisibleChanged: if (visible) { Survival.radios.scan(); kits.refresh() }

    C.ScrollView {
        anchors.fill: parent
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            x: page.narrow ? Look.space4 : Look.space5
            y: page.narrow ? Look.space4 : Look.space5
            width: Math.min(1100, parent.width - 2 * x)
            spacing: Look.space4
            ColumnLayout {
                spacing: 2
                TaosText { text: qsTr("Devices"); font.pixelSize: Look.pageTitle; font.weight: Font.DemiBold
                           Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: qsTr("What each kind of device needs to be ready, checked against this machine.")
                           font.pixelSize: Look.body; color: Look.textSecondary }
            }
            GridLayout {
                Layout.fillWidth: true
                columns: page.narrow || width < 760 ? 1 : 2
                columnSpacing: Look.space4; rowSpacing: Look.space4
                Repeater {
                    model: page.kits.kits
                    delegate: GlassPanel {
                        id: kcard
                        required property var modelData
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        implicitHeight: kcol.implicitHeight + Look.space4 * 2
                        objectName: "kit-" + modelData.id
                        ColumnLayout {
                            id: kcol
                            anchors { fill: parent; margins: Look.space4 }
                            spacing: Look.space3
                            RowLayout {
                                spacing: Look.space3
                                Rectangle {
                                    Layout.alignment: Qt.AlignTop
                                    width: 40; height: 40; radius: Look.radiusCard - 3
                                    color: Look.field; border.width: 1; border.color: Look.hairline
                                    Glyph { anchors.centerIn: parent; name: kcard.modelData.id === "radio" ? "radio" : kcard.modelData.id === "pi" ? "cpu" : kcard.modelData.id === "home" ? "home" : "backpack"
                                            size: 20; stroke: 1.8; color: Look.textSecondary }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    TaosText { text: kcard.modelData.title; font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                                    TaosText { text: kcard.modelData.hardware; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary }
                                }
                                TaosText { text: qsTr("%1 of %2").arg(kcard.modelData.ready).arg(kcard.modelData.total); font.pixelSize: Look.body; font.weight: Font.Medium
                                           color: kcard.modelData.ready === kcard.modelData.total ? Look.success : Look.textSecondary; font.features: { "tnum": 1 }
                                           Accessible.ignored: false; Accessible.role: Accessible.StaticText
                                           Accessible.name: qsTr("%1 of %2 checks ready").arg(kcard.modelData.ready).arg(kcard.modelData.total) }
                            }
                            ProgressTrack { Layout.fillWidth: true; value: kcard.modelData.total > 0 ? kcard.modelData.ready / kcard.modelData.total : 0
                                            tone: kcard.modelData.ready === kcard.modelData.total ? "success" : "accent" }
                            TaosText { text: kcard.modelData.summary; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                            Repeater {
                                model: kcard.modelData.items
                                delegate: RowLayout {
                                    id: irow
                                    required property var modelData
                                    Layout.fillWidth: true
                                    spacing: Look.space3
                                    Accessible.role: Accessible.ListItem
                                    Accessible.name: modelData.title + ", " + (modelData.status === "ok" ? qsTr("ready") : modelData.status === "missing" ? qsTr("missing") : qsTr("unknown")) + ". " + modelData.detail
                                    Glyph {
                                        Layout.alignment: Qt.AlignTop; Layout.topMargin: 1
                                        name: irow.modelData.status === "ok" ? "check-circle" : irow.modelData.status === "missing" ? "circle-x" : "circle-dashed"
                                        size: 18; stroke: 1.9
                                        color: irow.modelData.status === "ok" ? Look.success : irow.modelData.status === "missing" ? Look.warning : Look.textTertiary
                                    }
                                    ColumnLayout {
                                        Layout.fillWidth: true; spacing: 1
                                        TaosText { text: irow.modelData.title; Layout.fillWidth: true; font.pixelSize: Look.body; font.weight: Font.Medium; wrapMode: Text.Wrap }
                                        TaosText { text: irow.modelData.detail; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            Item { Layout.preferredHeight: Look.space4 }
        }
    }
}
