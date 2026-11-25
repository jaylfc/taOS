// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Ready-made collections: seven subjects, three sizes each (Essential, Standard, Comprehensive; each includes the one
// before). One button queues every pack of a tier that is not installed yet, one after another.
C.ScrollView {
    id: page
    property bool narrow: false
    contentWidth: availableWidth
    clip: true
    readonly property var engine: Survival.collections
    property string message: ""
    ColumnLayout {
        width: page.availableWidth
        spacing: Look.space3
        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                   text: qsTr("Pick a size for a subject and everything in it is downloaded and checked, one pack after another. Sizes are the publishers' rounded figures.") }
        TaosText { visible: page.message !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.message; font.pixelSize: Look.body; color: Look.warning }
        Repeater {
            model: page.engine.categories
            delegate: GlassPanel {
                id: cat
                required property var modelData
                Layout.fillWidth: true
                implicitHeight: catCol.implicitHeight + Look.space4 * 2
                ColumnLayout {
                    id: catCol
                    anchors { fill: parent; margins: Look.space4 }
                    spacing: Look.space3
                    TaosText { text: cat.modelData.name; font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
                    TaosText { text: cat.modelData.description; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary }
                    Repeater {
                        model: cat.modelData.tiers
                        delegate: ColumnLayout {
                            id: tierRow
                            required property var modelData
                            property bool accept: false
                            readonly property var info: { Survival.packs.packs; return page.engine.tier(modelData.slug) }
                            Layout.fillWidth: true; spacing: Look.space2
                            Rectangle { Layout.fillWidth: true; height: 1; color: Look.hairline }
                            RowLayout {
                                Layout.fillWidth: true; spacing: Look.space3
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    TaosText { text: tierRow.modelData.name + (tierRow.modelData.recommended ? "  " + qsTr("(start here)") : ""); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold }
                                    TaosText { text: qsTr("%1 in all, %2 installed, %3 still to download").arg(tierRow.info.count).arg(tierRow.info.installed).arg(tierRow.info.remainingText || "0 B")
                                               font.pixelSize: Look.small; color: Look.textSecondary; Layout.fillWidth: true; wrapMode: Text.Wrap }
                                }
                                PillButton {
                                    text: tierRow.info.installed === tierRow.info.count ? qsTr("Installed") : qsTr("Get this size")
                                    variant: "primary"; glyph: "download"; compact: true
                                    enabled: tierRow.info.installed < tierRow.info.count && (tierRow.info.restrictiveCount === 0 || tierRow.accept)
                                    accessibleName: qsTr("Download the %1 size of %2").arg(tierRow.modelData.name).arg(cat.modelData.name)
                                    onClicked: page.message = page.engine.installTier(tierRow.modelData.slug, tierRow.accept)
                                }
                            }
                            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: tierRow.modelData.description; font.pixelSize: Look.small; color: Look.textTertiary }
                            TaosCheck {
                                visible: tierRow.info.restrictiveCount > 0 && tierRow.info.installed < tierRow.info.count
                                text: tierRow.info.restrictiveCount === 1 ? qsTr("I accept the licence of the 1 pack in this size that restricts use (non-commercial, no changes, or unknown). Browse shows it.") : qsTr("I accept the licences of the %1 packs in this size that restrict use (non-commercial, no changes, or unknown). Browse shows each.").arg(tierRow.info.restrictiveCount)
                                checked: tierRow.accept; onToggled: tierRow.accept = checked
                            }
                        }
                    }
                }
            }
        }
        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textTertiary
                   text: qsTr("Collections follow the tiers of Project N.O.M.A.D. (Apache-2.0). The packs come straight from download.kiwix.org.") }
    }
}
