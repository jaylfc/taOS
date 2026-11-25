// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// One kit: its items by category, and a check against a scenario for this household.
ColumnLayout {
    id: root
    property bool narrow: false
    signal closeRequested()
    readonly property var gear: Survival.gear
    readonly property var kit: gear.kit
    property string tab: gear.tab
    onTabChanged: if (gear.tab !== tab) gear.tab = tab
    property string editId: ""        // item being changed; "new" for a new one
    property string scenarioId: ""
    property string note: ""
    spacing: Look.space3
    onKitChanged: if (scenarioId === "" && kit.id) scenarioId = kit.scenario !== "" ? kit.scenario : defaultScenario()
    function defaultScenario() { for (const s of gear.scenarios) if (s.kitType === kit.type) return s.id; return gear.scenarios.length ? gear.scenarios[0].id : "" }
    function itemById(id) { for (const i of kit.items || []) if (i.id === id) return i; return ({}) }
    readonly property var byCategory: {
        const m = {}, order = []
        for (const i of kit.items || []) { if (!m[i.category]) { m[i.category] = []; order.push(i.category) } m[i.category].push(i) }
        return order.sort().map(c => ({category: c, items: m[c]}))
    }
    function catTitle(c) { return ({water: qsTr("Water"), food: qsTr("Food"), shelter: qsTr("Shelter"), warmth: qsTr("Warmth"), light: qsTr("Light"), power: qsTr("Power"),
        firstaid: qsTr("First aid"), medication: qsTr("Medication"), tools: qsTr("Tools"), comms: qsTr("Communication"), documents: qsTr("Documents"),
        hygiene: qsTr("Hygiene"), clothing: qsTr("Clothing")})[c] || c }

    RowLayout {
        Layout.fillWidth: true; spacing: Look.space2
        TaosButton { visible: root.narrow; glyph: "chevron-left"; text: qsTr("Kits"); variant: "ghost"; onClicked: root.closeRequested() }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 2
            TaosText { text: root.kit.name || ""; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Look.pageTitle; font.weight: Font.DemiBold
                       Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
            TaosText { font.pixelSize: Look.body; color: Look.textSecondary; Layout.fillWidth: true; wrapMode: Text.Wrap
                       text: (root.kit.items ? qsTr("%1 items").arg(root.kit.items.length) : "") + (root.kit.weightText ? "  " + root.kit.weightText : "") + (root.kit.shared ? "  " + qsTr("Shared with the household") : "") }
        }
        PillButton { visible: !!root.kit.mine; compact: true; text: root.kit.shared ? qsTr("Stop sharing") : qsTr("Share"); glyph: "users"
                     accessibleName: root.kit.shared ? qsTr("Stop sharing this kit with the household") : qsTr("Share this kit with the household"); onClicked: root.gear.setShared(root.kit.id, !root.kit.shared) }
        PillButton { visible: !!root.kit.mine; compact: true; text: confirm.running ? qsTr("Delete for good?") : qsTr("Delete"); glyph: "trash"
                     onClicked: { if (confirm.running) { confirm.stop(); root.gear.removeKit(root.kit.id); root.closeRequested() } else confirm.restart() }
                     Timer { id: confirm; interval: 4000 } }
    }
    Segmented {
        accessibleName: qsTr("Kit view")
        model: [{id: "items", text: qsTr("Items"), glyph: "backpack"}, {id: "check", text: qsTr("Check against a list"), glyph: "check-circle"}]
        currentId: root.tab
        onActivated: (id) => { root.tab = id; if (id === "check" && root.scenarioId !== "") root.gear.runAudit(root.scenarioId) }
    }

    // ---- items
    C.ScrollView {
        visible: root.tab === "items"
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: Look.space2
            PillButton { text: qsTr("Add an item"); glyph: "plus"; variant: "primary"; visible: root.editId === ""; onClicked: root.editId = "new" }
            ItemForm { visible: root.editId !== ""; Layout.fillWidth: true; item: root.editId === "new" ? ({}) : root.itemById(root.editId); onDone: root.editId = "" }
            EmptyState { visible: (root.kit.items || []).length === 0 && root.editId === ""; Layout.fillWidth: true; Layout.topMargin: Look.space5
                         glyph: "backpack"; title: qsTr("Nothing in this kit yet"); detail: qsTr("Add what you have, or open Check against a list to see what a good kit holds.") }
            Repeater {
                model: root.byCategory
                delegate: ColumnLayout {
                    id: grp
                    required property var modelData
                    Layout.fillWidth: true; spacing: 2
                    TaosText { text: root.catTitle(grp.modelData.category); Layout.topMargin: Look.space3; font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary
                               font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0 }
                    Repeater {
                        model: grp.modelData.items
                        delegate: ListRow {
                            id: irow
                            required property var modelData
                            Layout.fillWidth: true
                            glyph: modelData.category === "medication" ? "shield" : "backpack"
                            title: modelData.name
                            subtitle: (modelData.qty % 1 === 0 ? modelData.qty : modelData.qty.toFixed(1)) + (modelData.unit !== "" ? " " + modelData.unit : "") + (modelData.location !== "" ? "   " + modelData.location : "")
                            detail: modelData.expiry === "expired" ? qsTr("Expired %1").arg(modelData.expires) : modelData.expiry === "soon" ? qsTr("Best before %1 (%2 days)").arg(modelData.expires).arg(modelData.daysLeft)
                                  : modelData.expires !== "" ? qsTr("Best before %1").arg(modelData.expires) : ""
                            tone: modelData.expiry === "expired" ? "danger" : modelData.expiry === "soon" ? "warning" : "neutral"
                            onClicked: root.editId = modelData.id
                            trailing: Component {
                                RowLayout {
                                    spacing: Look.space2
                                    Chip { visible: irow.modelData.private; text: qsTr("Private"); glyph: "shield" }
                                    Image { visible: irow.modelData.photo !== ""; source: root.gear.photoUrl(irow.modelData.photo); sourceSize.width: 64; sourceSize.height: 64
                                            Layout.preferredWidth: 32; Layout.preferredHeight: 32; fillMode: Image.PreserveAspectCrop; asynchronous: true }
                                    TaosButton { visible: irow.modelData.rotateMonths > 0; compact: true; text: qsTr("Swapped"); accessibleName: qsTr("Mark %1 as swapped today").arg(irow.modelData.name)
                                                 onClicked: root.gear.rotateItem(irow.modelData.id) }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ---- check
    C.ScrollView {
        visible: root.tab === "check"
        Layout.fillWidth: true; Layout.fillHeight: true
        contentWidth: availableWidth
        clip: true
        ColumnLayout {
            width: parent.width
            spacing: Look.space3
            TaosCombo {
                Layout.fillWidth: true
                accessibleName: qsTr("List to check against")
                model: root.gear.scenarios
                textRole: "title"; valueRole: "id"
                currentIndex: { for (let i = 0; i < root.gear.scenarios.length; i++) if (root.gear.scenarios[i].id === root.scenarioId) return i; return 0 }
                onActivated: { root.scenarioId = currentValue; root.gear.runAudit(currentValue) }
            }
            GlassPanel {
                visible: !!root.gear.audit.rows
                Layout.fillWidth: true
                implicitHeight: sumCol.implicitHeight + Look.space4 * 2
                ColumnLayout {
                    id: sumCol
                    anchors { fill: parent; margins: Look.space4 }
                    spacing: Look.space2
                    RowLayout {
                        TaosText { text: qsTr("%1 percent ready").arg(root.gear.audit.score || 0); font.pixelSize: Look.heading; font.weight: Font.DemiBold; font.features: { "tnum": 1 } }
                        Item { Layout.fillWidth: true }
                        TaosText { text: qsTr("For %1 and %2").arg((root.gear.audit.people || 1) === 1 ? qsTr("1 person") : qsTr("%1 people").arg(root.gear.audit.people)).arg((root.gear.audit.pets || 0) === 1 ? qsTr("1 pet") : qsTr("%1 pets").arg(root.gear.audit.pets || 0)); font.pixelSize: Look.small; color: Look.textSecondary }
                    }
                    ProgressTrack { Layout.fillWidth: true; value: (root.gear.audit.score || 0) / 100; tone: (root.gear.audit.score || 0) >= 90 ? "success" : "accent" }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary; text: (root.gear.audit.scenario || {}).summary || "" }
                }
            }
            Repeater {
                model: root.gear.audit.rows || []
                delegate: RowLayout {
                    id: arow
                    required property var modelData
                    Layout.fillWidth: true; spacing: Look.space3
                    Accessible.role: Accessible.ListItem
                    Accessible.name: modelData.name + ", " + (modelData.status === "ok" ? qsTr("enough") : modelData.status === "short" ? qsTr("not enough") : qsTr("missing"))
                    Glyph { Layout.alignment: Qt.AlignTop; name: modelData.status === "ok" ? "check-circle" : modelData.status === "short" ? "circle-dashed" : "circle-x"; size: 18; stroke: 1.9
                            color: modelData.status === "ok" ? Look.success : modelData.status === "short" ? Look.warning : Look.danger }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 1
                        TaosText { text: arow.modelData.name; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; font.weight: Font.Medium }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                   text: qsTr("Have %1 of %2 %3").arg(arow.modelData.have).arg(arow.modelData.needed).arg(arow.modelData.unit) + (arow.modelData.expiredIgnored > 0 ? "  " + qsTr("(%1 expired, not counted)").arg(arow.modelData.expiredIgnored) : "") }
                        TaosText { visible: arow.modelData.note !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textTertiary; text: arow.modelData.note }
                    }
                }
            }
            RowLayout {
                visible: !!root.gear.audit.rows; Layout.fillWidth: true; spacing: Look.space2
                PillButton { text: qsTr("Draft a shopping list"); glyph: "plus"; variant: "primary"; onClicked: root.note = root.gear.draftShopping(root.scenarioId) }
                TaosText { visible: root.note !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: root.note; font.pixelSize: Look.body; color: Look.textSecondary }
            }
            Repeater {
                model: root.gear.shopping.filter(l => l.kitId === root.kit.id)
                delegate: GlassPanel {
                    id: shop
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: shopCol.implicitHeight + Look.space3 * 2
                    ColumnLayout {
                        id: shopCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout {
                            TaosText { text: qsTr("Shopping list: %1").arg(shop.modelData.scenario); Layout.fillWidth: true; font.pixelSize: Look.title; font.weight: Font.DemiBold; wrapMode: Text.Wrap }
                            Chip { text: shop.modelData.status === "approved" ? qsTr("Approved") : shop.modelData.agent ? qsTr("Draft from an agent") : qsTr("Draft"); tone: shop.modelData.status === "approved" ? "success" : "warning" }
                        }
                        Repeater {
                            model: shop.modelData.items
                            delegate: TaosText { required property var modelData; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: modelData.qty + " " + modelData.unit + "  " + modelData.name }
                        }
                        RowLayout {
                            spacing: Look.space2
                            PillButton { visible: shop.modelData.status === "draft"; text: qsTr("Approve"); variant: "primary"; onClicked: root.note = root.gear.approveShopping(shop.modelData.id) }
                            PillButton { text: qsTr("Remove"); glyph: "trash"; onClicked: root.gear.removeShopping(shop.modelData.id) }
                        }
                    }
                }
            }
            // where the list comes from
            TaosText { visible: !!root.gear.audit.rows; text: qsTr("Where this list comes from"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary
                       font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; Layout.topMargin: Look.space3 }
            Repeater {
                model: (root.gear.audit.scenario || {}).sources || []
                delegate: ColumnLayout {
                    id: src
                    required property var modelData
                    Layout.fillWidth: true; spacing: 1
                    TaosText { text: src.modelData.title + ", " + src.modelData.publisher; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; font.weight: Font.Medium }
                    TaosText { visible: src.modelData.url !== ""; text: src.modelData.url; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere; font.pixelSize: Look.small; color: Look.textSecondary }
                    TaosText { text: src.modelData.terms; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.caption; color: Look.textTertiary }
                }
            }
        }
    }
}
