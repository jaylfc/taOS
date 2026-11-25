// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// A radio's own settings, matching the core of the official clients. Meshtastic: owner names, region, modem preset, role,
// channels (key show, rotate, share as QR), position broadcast. MeshCore: name, radio parameters, contacts and rooms,
// advert settings. Values come from the engine; a value the engine did not read says so (never a default). Changes are
// staged here and only go out through the engine's write path, which stays off with a visible reason until the engine
// reports write support, a radio is connected and the owner confirms.
C.ScrollView {
    id: root
    property var nav
    property string radioId: ""
    readonly property var tak: Survival.tak
    readonly property var cfg: { tak.radios; return tak.config(radioId) }
    readonly property var vals: cfg.values || ({})
    readonly property var radio: { for (const r of tak.radios) if (r.id === radioId) return r; return null }
    readonly property string network: radio ? radio.transport : (vals.network || "meshtastic")
    readonly property bool ok: cfg.state === "ok"
    property var staged: ({})
    property int stagedCount: Object.keys(staged).length
    property bool confirming: false
    property var shown: ({})          // channel index -> key revealed
    property int qrChannel: -1
    contentWidth: availableWidth
    clip: true
    Component.onCompleted: if (radioId !== "") tak.requestConfig(radioId)
    onRadioIdChanged: if (radioId !== "") tak.requestConfig(radioId)
    onNavChanged: if (nav && nav.demo.qr !== undefined) { qrChannel = Number(nav.demo.qr); if (nav.demo.showkey !== undefined) { const s = {}; s[Number(nav.demo.showkey)] = true; shown = s } }
    function getPath(o, p) {
        const m = /^channels\.([0-9]+)\.(.+)$/.exec(p)
        if (m) { for (const c of (o.channels || [])) if (String(c.index) === m[1]) return c[m[2]]; return undefined }
        let c = o; for (const k of p.split(".")) { if (c === undefined || c === null) return undefined; c = c[k] } return c
    }
    function stage(path, v) {
        const s = Object.assign({}, staged)
        if (String(getPath(vals, path)) === String(v)) delete s[path]; else s[path] = v
        staged = s
    }
    function current(path) { return staged[path] !== undefined ? staged[path] : getPath(vals, path) }
    // which settings taOS can write on this radio: the engine's own list (a path with <n> stands for every channel)
    function canWrite(path) {
        const w = vals.writable
        if (!w) return false
        for (const t of w) {
            if (t === path) return true
            if (t.indexOf("<n>") >= 0 && new RegExp("^" + t.replace(/[.]/g, "\\.").replace("<n>", "[0-9]+") + "$").test(path)) return true
        }
        return false
    }
    function warnings() {
        const out = []
        if (staged["lora.region"] !== undefined)
            out.push(qsTr("Region sets the radio frequencies. Choose the one that is legal where you are. A radio on another region cannot hear this one."))
        if (staged["lora.modem_preset"] !== undefined)
            out.push(qsTr("The modem preset must match the other radios on your mesh, or you will not hear each other."))
        for (const k of Object.keys(staged)) if (k.indexOf("channels.") === 0 && k.slice(-5) === ".name")
            out.push(qsTr("Renaming a channel changes the name other radios must also use to join it."))
        return out.filter((v, i, a) => a.indexOf(v) === i)
    }
    function opts(name) { return vals.options && vals.options[name] ? vals.options[name] : [] }
    readonly property var specs: network === "meshcore" ? [
        {title: qsTr("Name"), fields: [{path: "name", label: qsTr("Node name"), type: "text"}]},
        {title: qsTr("Radio"), fields: [{path: "radio.region_preset", label: qsTr("Region preset"), type: "combo", opt: "region_preset"},
                                        {path: "radio.freq_mhz", label: qsTr("Frequency (MHz)"), type: "text"}, {path: "radio.bw_khz", label: qsTr("Bandwidth (kHz)"), type: "text"},
                                        {path: "radio.sf", label: qsTr("Spreading factor"), type: "text"}, {path: "radio.cr", label: qsTr("Coding rate"), type: "text"}]},
        {title: qsTr("Advert"), fields: [{path: "advert.interval_min", label: qsTr("Advert interval (minutes)"), type: "text"}, {path: "advert.share_location", label: qsTr("Share position in adverts"), type: "toggle"}]}
    ] : [
        {title: qsTr("Owner"), fields: [{path: "owner.long_name", label: qsTr("Long name"), type: "text"}, {path: "owner.short_name", label: qsTr("Short name"), type: "text"}]},
        {title: qsTr("Radio"), fields: [{path: "lora.region", label: qsTr("Region"), type: "combo", opt: "region"}, {path: "lora.modem_preset", label: qsTr("Modem preset"), type: "combo", opt: "modem_preset"}]},
        {title: qsTr("Device"), fields: [{path: "device.role", label: qsTr("Device role"), type: "combo", opt: "role"}]},
        {title: qsTr("Position"), fields: [{path: "position.broadcast_secs", label: qsTr("Broadcast every (seconds)"), type: "text"}, {path: "position.precision_bits", label: qsTr("Position precision (bits)"), type: "text"}]}
    ]

    ColumnLayout {
        x: Look.space3
        width: parent.width - Look.space3 * 2
        spacing: Look.space3
        RowLayout {
            Layout.topMargin: Look.space3; spacing: Look.space2
            Chip { text: root.radio ? root.radio.networkName : qsTr("Radio") }
            TaosText { text: root.radio ? root.radio.name : root.radioId; font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
        }
        GlassPanel {
            visible: !root.ok
            objectName: "settingsUnavailable"
            Layout.fillWidth: true
            implicitHeight: noCol.implicitHeight + Look.space3 * 2
            ColumnLayout {
                id: noCol
                anchors { fill: parent; margins: Look.space3 }
                spacing: Look.space2
                RowLayout {
                    spacing: Look.space2
                    Glyph { name: root.cfg.state === "error" ? "alert" : "info"; size: 18; stroke: 1.8; color: root.cfg.state === "error" ? Look.danger : Look.textSecondary }
                    TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold
                               text: root.cfg.state === "loading" ? qsTr("Reading the radio's settings") : root.cfg.state === "error" ? qsTr("Could not read the settings") : qsTr("Settings are not read from the radio yet") }
                }
                TaosText { Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                           text: root.cfg.note !== "" ? root.cfg.note : root.cfg.state === "loading" ? qsTr("This takes a moment.") : qsTr("This engine does not read radio settings yet. The fields below show what will be here; none of them has a value.") }
            }
        }
        Repeater {
            model: root.specs
            delegate: ColumnLayout {
                id: sec
                required property var modelData
                Layout.fillWidth: true; spacing: Look.space2
                TakLabel { text: sec.modelData.title }
                GlassPanel {
                    Layout.fillWidth: true
                    implicitHeight: fcol.implicitHeight + Look.space2 * 2
                    ColumnLayout {
                        id: fcol
                        anchors { fill: parent; margins: Look.space3; topMargin: Look.space2; bottomMargin: Look.space2 }
                        spacing: Look.space2
                        Repeater {
                            model: sec.modelData.fields
                            delegate: RowLayout {
                                id: fr
                                required property var modelData
                                readonly property var v: { root.staged; root.vals; return root.current(modelData.path) }
                                readonly property bool known: v !== undefined && v !== null
                                Layout.fillWidth: true; spacing: Look.space3
                                objectName: "field-" + modelData.path
                                TaosText { Layout.preferredWidth: 130; Layout.alignment: Qt.AlignVCenter; wrapMode: Text.Wrap; font.pixelSize: Look.body; text: fr.modelData.label }
                                TaosText { visible: !fr.known || !root.ok; Layout.fillWidth: true; font.pixelSize: Look.small; color: Look.textTertiary; text: qsTr("Not read from the radio yet") }
                                TaosField {
                                    visible: fr.known && root.ok && fr.modelData.type === "text"
                                    Layout.fillWidth: true
                                    text: fr.known ? String(fr.v) : ""
                                    enabled: root.canWrite(fr.modelData.path)
                                    accessibleName: fr.modelData.label
                                    onEditingFinished: root.stage(fr.modelData.path, text)
                                }
                                TaosCombo {
                                    visible: fr.known && root.ok && fr.modelData.type === "combo"
                                    Layout.fillWidth: true
                                    enabled: root.canWrite(fr.modelData.path)
                                    accessibleName: fr.modelData.label
                                    model: root.opts(fr.modelData.opt || "").length > 0 ? root.opts(fr.modelData.opt) : (fr.known ? [String(fr.v)] : [])
                                    currentIndex: Math.max(0, model.indexOf(String(fr.v)))
                                    onActivated: root.stage(fr.modelData.path, model[currentIndex])
                                }
                                TaosCheck {
                                    visible: fr.known && root.ok && fr.modelData.type === "toggle"
                                    checked: fr.known && fr.v === true
                                    enabled: root.canWrite(fr.modelData.path)
                                    text: checked ? qsTr("On") : qsTr("Off")
                                    onToggled: root.stage(fr.modelData.path, checked)
                                }
                            }
                        }
                    }
                }
            }
        }

        // ---- channels (both networks; MeshCore ones are read only)
        ColumnLayout {
            Layout.fillWidth: true; spacing: Look.space2
            TakLabel { text: qsTr("Channels") }
            TaosText { visible: !root.ok || !(root.vals.channels && root.vals.channels.length > 0); Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                       text: qsTr("Channels are not read from the radio yet") }
            Repeater {
                model: root.ok && root.vals.channels ? root.vals.channels : []
                delegate: GlassPanel {
                    id: ch
                    required property var modelData
                    required property int index
                    objectName: "channel-" + index
                    Layout.fillWidth: true
                    implicitHeight: chCol.implicitHeight + Look.space3 * 2
                    ColumnLayout {
                        id: chCol
                        anchors { fill: parent; margins: Look.space3 }
                        spacing: Look.space2
                        RowLayout {
                            spacing: Look.space2
                            TaosText { text: ch.modelData.name !== "" ? ch.modelData.name : qsTr("Channel %1").arg(ch.index); font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
                            Chip { text: ch.modelData.role === "PRIMARY" ? qsTr("Primary") : ch.modelData.role === "SECONDARY" ? qsTr("Secondary") : String(ch.modelData.role) }
                        }
                        RowLayout {
                            visible: root.canWrite("channels." + ch.index + ".name")
                            spacing: Look.space2
                            TaosText { text: qsTr("Name"); font.pixelSize: Look.body; color: Look.textSecondary; Layout.preferredWidth: 40 }
                            TaosField { objectName: "channelName-" + ch.index; Layout.fillWidth: true; text: root.staged["channels." + ch.index + ".name"] !== undefined ? root.staged["channels." + ch.index + ".name"] : ch.modelData.name
                                        accessibleName: qsTr("Channel %1 name").arg(ch.index); onEditingFinished: root.stage("channels." + ch.index + ".name", text) }
                        }
                        RowLayout {
                            spacing: Look.space2
                            TaosText { text: qsTr("Key"); font.pixelSize: Look.body; color: Look.textSecondary; Layout.preferredWidth: 40 }
                            TaosText { Layout.fillWidth: true; font.family: "monospace"; font.pixelSize: Look.small; elide: Text.ElideMiddle
                                       text: root.shown[ch.index] ? String(ch.modelData.psk_b64) : "••••••••••••" }
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: Look.space2
                            PillButton { objectName: "pskShow-" + ch.index; compact: true; text: root.shown[ch.index] ? qsTr("Hide key") : qsTr("Show key")
                                         onClicked: { const s = Object.assign({}, root.shown); s[ch.index] = !s[ch.index]; root.shown = s } }
                            PillButton { objectName: "pskRotate-" + ch.index; compact: true; visible: root.canWrite("channels." + ch.index + ".psk"); text: root.staged["channels." + ch.index + ".psk"] === "rotate" ? qsTr("Rotation staged") : qsTr("Rotate key"); glyph: "refresh"
                                         onClicked: root.stage("channels." + ch.index + ".psk", "rotate") }
                            PillButton { objectName: "pskShare-" + ch.index; compact: true; text: root.qrChannel === ch.index ? qsTr("Hide QR") : qsTr("Share as QR"); glyph: "grid"
                                         enabled: ch.modelData.share_url !== undefined && ch.modelData.share_url !== ""
                                         onClicked: root.qrChannel = root.qrChannel === ch.index ? -1 : ch.index }
                        }
                        TaosText { visible: root.staged["channels." + ch.index + ".psk"] === "rotate"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.warning
                                   text: qsTr("A new key is made when you apply. Everyone using this channel needs the new key.") }
                        ColumnLayout {
                            visible: root.qrChannel === ch.index
                            Layout.fillWidth: true; spacing: Look.space2
                            QrCode { objectName: "channelQr"; Layout.alignment: Qt.AlignHCenter; implicitWidth: 200; implicitHeight: 200
                                     rows: root.qrChannel === ch.index && ch.modelData.share_url ? root.tak.qr(ch.modelData.share_url) : []
                                     accessibleName: qsTr("QR code to join %1").arg(ch.modelData.name) }
                            TaosText { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                                       text: qsTr("Anyone who scans this joins the channel and can read it. Show it only to people you trust.") }
                        }
                    }
                }
            }
        }

        // ---- MeshCore contacts and rooms
        ColumnLayout {
            visible: root.network === "meshcore"
            Layout.fillWidth: true; spacing: Look.space2
            TakLabel { text: qsTr("Contacts and rooms") }
            TaosText { visible: !root.ok || !(root.vals.contacts && root.vals.contacts.length > 0); Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textTertiary
                       text: qsTr("Contacts are not read from the radio yet") }
            Repeater {
                model: root.ok && root.vals.contacts ? root.vals.contacts : []
                delegate: ListRow {
                    required property var modelData
                    Layout.fillWidth: true; clickable: false
                    glyph: modelData.type === "room" ? "users" : modelData.type === "repeater" ? "radio" : "user"
                    title: modelData.name
                    subtitle: modelData.type === "room" ? qsTr("Room") : modelData.type === "repeater" ? qsTr("Repeater") : qsTr("Contact")
                    detail: modelData.hops !== undefined ? (modelData.hops === 1 ? qsTr("1 hop away") : qsTr("%1 hops away").arg(modelData.hops)) : ""
                }
            }
        }

        // ---- applying
        GlassPanel {
            Layout.fillWidth: true
            objectName: "applyBar"
            implicitHeight: applyCol.implicitHeight + Look.space3 * 2
            ColumnLayout {
                id: applyCol
                anchors { fill: parent; margins: Look.space3 }
                spacing: Look.space2
                TaosText { font.pixelSize: Look.bodyMedium; font.weight: Font.DemiBold
                           text: root.stagedCount === 0 ? qsTr("No changes") : (root.stagedCount === 1 ? qsTr("1 change ready to apply") : qsTr("%1 changes ready to apply").arg(root.stagedCount)) }
                TaosText { objectName: "applyReason"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.textSecondary
                           text: !root.tak.configWritable ? qsTr("Will apply when a radio is connected and you confirm. The mesh engine is receive-only or too old to write settings, so nothing can be changed from here.")
                                 : !(root.radio && root.radio.state === "connected") ? qsTr("Will apply when a radio is connected and you confirm.")
                                 : qsTr("Review the changes, then confirm. The radio may restart.") }
                Repeater {
                    model: root.warnings()
                    delegate: RowLayout {
                        required property string modelData
                        Layout.fillWidth: true; spacing: Look.space2
                        Glyph { name: "alert"; size: 14; stroke: 2; color: Look.warning; Layout.alignment: Qt.AlignTop }
                        TaosText { objectName: "settingWarning"; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small; color: Look.warning; text: modelData }
                    }
                }
                ColumnLayout {
                    visible: root.confirming
                    Layout.fillWidth: true; spacing: Look.space1
                    Repeater { model: Object.keys(root.staged); delegate: TaosText { required property string modelData; Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: Look.small
                                                                                     text: modelData + ": " + root.staged[modelData] } }
                }
                RowLayout {
                    spacing: Look.space2
                    PillButton { objectName: "reviewApply"; compact: true; variant: "primary"; visible: !root.confirming; text: qsTr("Review and apply")
                                 enabled: root.tak.configWritable && root.stagedCount > 0 && root.radio !== null && root.radio.state === "connected"; onClicked: root.confirming = true }
                    PillButton { objectName: "confirmApply"; compact: true; variant: "primary"; visible: root.confirming; text: qsTr("Apply to the radio")
                                 onClicked: { const e = root.tak.applyConfig(root.radioId, root.staged); if (e === "") { root.staged = ({}); root.confirming = false } } }
                    PillButton { compact: true; visible: root.confirming; text: qsTr("Cancel"); variant: "outline"; onClicked: root.confirming = false }
                    PillButton { compact: true; visible: root.stagedCount > 0 && !root.confirming; text: qsTr("Discard changes"); variant: "outline"; onClicked: root.staged = ({}) }
                }
            }
        }
        Item { Layout.preferredHeight: Look.space4 }
    }
}
