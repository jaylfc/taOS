// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// One item of a kit: add it or change it. Dates are written year-month-day. Medication is always private to its owner.
GlassPanel {
    id: form
    property var item: ({})           // empty for a new item
    property string error: ""
    signal done()
    readonly property var gear: Survival.gear
    readonly property bool editing: !!item.id
    implicitHeight: col.implicitHeight + Look.space4 * 2
    function value(k, d) { return item[k] !== undefined ? item[k] : d }
    ColumnLayout {
        id: col
        anchors { fill: parent; margins: Look.space4 }
        spacing: Look.space3
        TaosText { text: form.editing ? qsTr("Change item") : qsTr("New item"); font.pixelSize: Look.cardTitle; font.weight: Font.DemiBold }
        TaosField { id: nameF; Layout.fillWidth: true; text: form.value("name", ""); placeholderText: qsTr("Name, for example Drinking water"); accessibleName: qsTr("Item name") }
        GridLayout {
            Layout.fillWidth: true; columns: 2; columnSpacing: Look.space2; rowSpacing: Look.space2
            TaosCombo { id: catF; Layout.fillWidth: true; accessibleName: qsTr("Category"); model: form.gear.categories
                        currentIndex: Math.max(0, form.gear.categories.indexOf(form.value("category", "tools"))) }
            TaosField { id: qtyF; Layout.fillWidth: true; text: form.value("qty", 1) + ""; placeholderText: qsTr("How many"); accessibleName: qsTr("Quantity") }
            TaosField { id: unitF; Layout.fillWidth: true; text: form.value("unit", ""); placeholderText: qsTr("Unit (L, pcs, meals)"); accessibleName: qsTr("Unit") }
            TaosField { id: expF; Layout.fillWidth: true; text: form.value("expires", ""); placeholderText: qsTr("Best before, 2027-03-31"); accessibleName: qsTr("Best before date") }
            TaosField { id: rotF; Layout.fillWidth: true; text: form.value("rotateMonths", 0) > 0 ? form.value("rotateMonths", 0) + "" : ""; placeholderText: qsTr("Swap every (months)"); accessibleName: qsTr("Swap every this many months") }
            TaosField { id: wF; Layout.fillWidth: true; text: form.value("weightG", 0) > 0 ? form.value("weightG", 0) + "" : ""; placeholderText: qsTr("Weight of one, grams"); accessibleName: qsTr("Weight of one in grams") }
        }
        TaosField { id: locF; Layout.fillWidth: true; text: form.value("location", ""); placeholderText: qsTr("Where it is kept (hall cupboard, boot)"); accessibleName: qsTr("Storage location") }
        TaosField { id: notesF; Layout.fillWidth: true; text: form.value("notes", ""); placeholderText: qsTr("Notes"); accessibleName: qsTr("Notes") }
        TaosCheck { id: privF; text: qsTr("Only I can see this item"); checked: form.value("private", false) || catF.currentText === "medication"; enabled: catF.currentText !== "medication" }
        TaosText { visible: catF.currentText === "medication"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                   text: qsTr("Medication is always private to the person it belongs to, even in a shared kit.") }
        RowLayout {
            visible: form.editing; Layout.fillWidth: true; spacing: Look.space2
            TaosField { id: photoF; Layout.fillWidth: true; placeholderText: qsTr("Path of a picture to attach"); accessibleName: qsTr("Path of a picture") }
            PillButton { compact: true; text: qsTr("Attach"); enabled: photoF.text.trim() !== ""; onClicked: { form.error = form.gear.attachPhoto(form.item.id, photoF.text); if (form.error === "") photoF.text = "" } }
        }
        TaosText { visible: form.error !== ""; Layout.fillWidth: true; wrapMode: Text.Wrap; text: form.error; font.pixelSize: Look.body; color: Look.danger }
        RowLayout {
            Layout.fillWidth: true; spacing: Look.space2
            PillButton { text: qsTr("Save"); variant: "primary"; onClicked: {
                const m = {id: form.item.id || "", name: nameF.text, category: catF.currentText, qty: qtyF.text, unit: unitF.text, expires: expF.text,
                           rotateMonths: parseInt(rotF.text) || 0, weightG: wF.text, location: locF.text, notes: notesF.text, "private": privF.checked,
                           key: form.value("key", ""), lastRotated: form.value("lastRotated", "")}
                form.error = form.gear.saveItem(m)
                if (form.error === "") form.done() } }
            PillButton { text: qsTr("Cancel"); variant: "outline"; onClicked: form.done() }
            Item { Layout.fillWidth: true }
            PillButton { visible: form.editing; text: qsTr("Delete"); glyph: "trash"; onClicked: { form.gear.removeItem(form.item.id); form.done() } }
        }
    }
}
