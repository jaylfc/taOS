// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// Kit: the gear you keep for the day things go wrong. Go-bags, a car kit, home supplies and a first aid kit hold
// items with quantities and dates; a list from public guidance shows what is missing for your household. Devices (what
// each machine needs to be ready) is one more kind of kit.
Item {
    id: page
    objectName: "page-kit"
    property bool narrow: false
    readonly property var gear: Survival.gear
    property bool showHousehold: false
    property string newType: "gobag"
    function focusSearch() {}
    Component.onCompleted: { Survival.radios.scan(); Survival.kits.refresh() }
    onVisibleChanged: if (visible) { Survival.radios.scan(); Survival.kits.refresh() }
    readonly property bool devices: gear.selected === "devices"
    readonly property bool hasDetail: gear.selected !== ""
    function typeGlyph(t) { return t === "car" ? "map" : t === "home" ? "home" : t === "firstaid" ? "shield" : t === "devices" ? "cpu" : "backpack" }
    function typeTitle(t) { return ({gobag: qsTr("Go-bag"), car: qsTr("Car kit"), home: qsTr("Home supplies"), firstaid: qsTr("First aid kit")})[t] || t }

    RowLayout {
        anchors.fill: parent
        spacing: 0
        // ---- list column
        C.ScrollView {
            id: listCol
            visible: !page.narrow || !page.hasDetail
            Layout.preferredWidth: page.narrow ? -1 : 380
            Layout.fillWidth: page.narrow
            Layout.fillHeight: true
            contentWidth: availableWidth
            clip: true
            ColumnLayout {
                width: listCol.availableWidth
                spacing: Look.space3
                ColumnLayout {
                    Layout.margins: page.narrow ? Look.space4 : Look.space5
                    Layout.bottomMargin: 0
                    spacing: 2
                    TaosText { text: qsTr("Kit"); font.pixelSize: Look.pageTitle; font.weight: Font.DemiBold
                               Accessible.ignored: false; Accessible.role: Accessible.Heading; Accessible.name: text }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary
                               text: qsTr("Your gear for water, food, warmth, light, first aid and papers, and how ready it is.") }
                }
                // readiness
                GlassPanel {
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                    implicitHeight: readyCol.implicitHeight + Look.space4 * 2
                    objectName: "readiness"
                    ColumnLayout {
                        id: readyCol
                        anchors { fill: parent; margins: Look.space4 }
                        spacing: Look.space2
                        RowLayout {
                            spacing: Look.space3
                            TaosText { text: page.gear.readiness.kitsCounted > 0 ? qsTr("%1%").arg(page.gear.readiness.score) : qsTr("No kits yet"); font.pixelSize: Look.display; font.weight: Font.DemiBold; font.features: { "tnum": 1 }
                                       Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: qsTr("Readiness %1 percent").arg(page.gear.readiness.score) }
                            TaosText { visible: page.gear.readiness.kitsCounted > 0; text: qsTr("ready"); font.pixelSize: Look.bodyMedium; color: Look.textSecondary; Layout.alignment: Qt.AlignBottom; Layout.bottomMargin: 6 }
                        }
                        ProgressTrack { Layout.fillWidth: true; value: (page.gear.readiness.score || 0) / 100; tone: (page.gear.readiness.score || 0) >= 90 ? "success" : "accent" }
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; color: Look.textSecondary; text: page.gear.readiness.next || "" }
                        Flow {
                            Layout.fillWidth: true; spacing: Look.space2
                            Chip { visible: page.gear.readiness.expired > 0; text: qsTr("%1 expired").arg(page.gear.readiness.expired); tone: "danger"; glyph: "alert" }
                            Chip { visible: page.gear.readiness.expiring > 0; text: qsTr("%1 expiring soon").arg(page.gear.readiness.expiring); tone: "warning"; glyph: "clock" }
                            Chip { visible: page.gear.readiness.missing > 0; text: qsTr("%1 things missing").arg(page.gear.readiness.missing); glyph: "circle-x" }
                        }
                    }
                }
                // household
                RowLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                    spacing: Look.space2
                    Glyph { name: "users"; size: 16; stroke: 1.8; color: Look.textSecondary }
                    TaosText { Layout.fillWidth: true; font.pixelSize: Look.body; color: Look.textSecondary; elide: Text.ElideRight
                               text: (page.gear.people.length === 1 ? qsTr("1 person") : qsTr("%1 people").arg(page.gear.people.length)) + ", " + (page.gear.pets === 1 ? qsTr("1 pet") : qsTr("%1 pets").arg(page.gear.pets)) }
                    PillButton { compact: true; text: page.showHousehold ? qsTr("Done") : qsTr("Household"); onClicked: page.showHousehold = !page.showHousehold }
                }
                GlassPanel {
                    visible: page.showHousehold
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                    implicitHeight: hhCol.implicitHeight + Look.space4 * 2
                    ColumnLayout {
                        id: hhCol
                        anchors { fill: parent; margins: Look.space4 }
                        spacing: Look.space2
                        TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                                   text: qsTr("Quantities in every list scale with the people and pets here. You see your own kits and what others share; medication stays private to its owner.") }
                        Repeater {
                            model: page.gear.people
                            delegate: RowLayout {
                                id: prow
                                required property var modelData
                                Layout.fillWidth: true; spacing: Look.space2
                                TaosText { Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: Look.body; font.weight: page.gear.me === prow.modelData.id ? Font.DemiBold : Font.Normal
                                           text: prow.modelData.name + (prow.modelData.kind === "child" ? "  " + qsTr("(child)") : "") + (page.gear.me === prow.modelData.id ? "  " + qsTr("(you)") : "") }
                                TaosButton { glyph: "trash"; compact: true; variant: "ghost"; visible: page.gear.people.length > 1 && prow.modelData.id !== page.gear.me; accessibleName: qsTr("Remove %1 from the household").arg(prow.modelData.name)
                                             onClicked: page.gear.removePerson(prow.modelData.id) }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Look.space2
                            TaosField { id: personName; Layout.fillWidth: true; placeholderText: qsTr("Add a person"); accessibleName: qsTr("Name of a person to add"); onAccepted: addPerson.clicked() }
                            TaosCheck { id: childCheck; text: qsTr("Child") }
                            TaosButton { id: addPerson; glyph: "plus"; variant: "primary"; enabled: personName.text.trim() !== ""; accessibleName: qsTr("Add this person")
                                         onClicked: { page.gear.addPerson(personName.text, childCheck.checked ? "child" : "adult"); personName.text = "" } }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Look.space2
                            TaosText { text: qsTr("Pets"); font.pixelSize: Look.body; Layout.fillWidth: true }
                            TaosButton { glyph: "minus"; compact: true; accessibleName: qsTr("One pet fewer"); enabled: page.gear.pets > 0; onClicked: page.gear.setPets(page.gear.pets - 1) }
                            TaosText { text: page.gear.pets; font.pixelSize: Look.bodyMedium; font.features: { "tnum": 1 }; Layout.preferredWidth: 24; horizontalAlignment: Text.AlignHCenter }
                            TaosButton { glyph: "plus"; compact: true; accessibleName: qsTr("One pet more"); onClicked: page.gear.setPets(page.gear.pets + 1) }
                        }
                    }
                }
                // reminders
                ColumnLayout {
                    visible: page.gear.reminders.length > 0
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                    spacing: Look.space1
                    TaosText { text: qsTr("Needs attention"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary; font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0 }
                    Repeater {
                        model: page.gear.reminders.slice(0, 5)
                        delegate: RowLayout {
                            id: rrow
                            required property var modelData
                            Layout.fillWidth: true; spacing: Look.space2
                            Glyph { name: rrow.modelData.kind === "expired" ? "alert" : "clock"; size: 16; stroke: 1.9; color: rrow.modelData.kind === "expired" ? Look.danger : Look.warning; Layout.alignment: Qt.AlignTop }
                            TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: rrow.modelData.title }
                        }
                    }
                }
                // kits
                TaosText { text: qsTr("Your kits"); Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary
                           font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0; Layout.topMargin: Look.space2 }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space3 : Look.space4; Layout.rightMargin: page.narrow ? Look.space3 : Look.space3
                    spacing: 2
                    Repeater {
                        model: page.gear.kits
                        delegate: ListRow {
                            id: krow
                            required property var modelData
                            Layout.fillWidth: true
                            objectName: "kit-" + modelData.id
                            glyph: page.typeGlyph(modelData.type)
                            title: modelData.name
                            subtitle: page.typeTitle(modelData.type) + "  " + qsTr("%1 items").arg(modelData.count) + (modelData.weightText !== "" ? "  " + modelData.weightText : "") + (modelData.shared ? "  " + qsTr("shared") : "") + (!modelData.mine ? "  " + qsTr("from %1").arg(modelData.owner) : "")
                            selected: page.gear.selected === modelData.id
                            onClicked: page.gear.select(modelData.id)
                            trailing: Component {
                                Chip { visible: krow.modelData.scored; text: krow.modelData.score + "%"; tone: krow.modelData.score >= 90 ? "success" : krow.modelData.score >= 50 ? "neutral" : "warning" }
                            }
                        }
                    }
                    ListRow {
                        Layout.fillWidth: true
                        objectName: "kit-devices"
                        glyph: "cpu"
                        title: qsTr("Devices")
                        subtitle: qsTr("What each machine needs to be ready, checked on this one")
                        selected: page.devices
                        onClicked: page.gear.select("devices")
                    }
                }
                // start
                EmptyState {
                    visible: page.gear.kits.length === 0
                    Layout.fillWidth: true; Layout.topMargin: Look.space4
                    glyph: "backpack"; title: qsTr("No kits yet"); detail: qsTr("Start from a list and fill in what you have. Quantities follow your household.")
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.leftMargin: page.narrow ? Look.space4 : Look.space5; Layout.rightMargin: page.narrow ? Look.space4 : Look.space4
                    Layout.bottomMargin: Look.space5
                    spacing: Look.space2
                    TaosText { text: qsTr("Start a kit from a list"); font.pixelSize: Look.small; font.weight: Font.DemiBold; color: Look.textTertiary; font.capitalization: Font.AllUppercase; font.letterSpacing: 1.0 }
                    Flow {
                        Layout.fillWidth: true; spacing: Look.space2
                        Repeater {
                            model: page.gear.scenarios
                            delegate: PillButton { required property var modelData; compact: true; text: modelData.title; glyph: "plus"
                                                   accessibleName: qsTr("Start a kit from the list %1").arg(modelData.title)
                                                   onClicked: page.gear.createFromScenario(modelData.id, "", false) }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: Look.space2
                        TaosField { id: newName; Layout.fillWidth: true; placeholderText: qsTr("Or a blank kit, with a name"); accessibleName: qsTr("Name of a new kit") }
                        TaosCombo { Layout.preferredWidth: 130; accessibleName: qsTr("Kind of kit"); model: ["gobag", "car", "home", "firstaid"]
                                    displayText: page.typeTitle(currentText); onActivated: page.newType = currentText
                                    delegate: C.ItemDelegate { required property string modelData; width: parent ? parent.width : 120; text: page.typeTitle(modelData); font.family: Look.family; font.pixelSize: Look.body } }
                        TaosButton { glyph: "plus"; variant: "primary"; enabled: newName.text.trim() !== ""; accessibleName: qsTr("Create the kit"); onClicked: { page.gear.addKit(newName.text, page.newType, false); newName.text = "" } }
                    }
                    TaosText { visible: page.gear.error !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: page.gear.error; font.pixelSize: Look.body; color: Look.danger }
                }
            }
        }
        Rectangle { visible: !page.narrow; Layout.fillHeight: true; width: 1; color: Look.hairline }
        // ---- detail
        Item {
            visible: !page.narrow || page.hasDetail
            Layout.fillWidth: true; Layout.fillHeight: true
            KitDetail {
                visible: page.hasDetail && !page.devices
                anchors { fill: parent; margins: page.narrow ? Look.space4 : Look.space5 }
                narrow: page.narrow
                onCloseRequested: page.gear.select("")
            }
            DevicesView {
                visible: page.devices
                anchors.fill: parent
                narrow: page.narrow
            }
            EmptyState {
                visible: !page.hasDetail
                anchors.centerIn: parent
                glyph: "backpack"; title: qsTr("Pick a kit"); detail: qsTr("Open a kit to see and change what is in it, or check it against a list.")
            }
            TaosButton { visible: page.devices && page.narrow; anchors { left: parent.left; top: parent.top; margins: Look.space3 } glyph: "chevron-left"; text: qsTr("Kits"); variant: "ghost"; onClicked: page.gear.select("") }
        }
    }
}
