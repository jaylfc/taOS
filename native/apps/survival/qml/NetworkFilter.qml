// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// All networks, or one: shown only when more than one network has a radio, a person or a conversation, so a person with a
// single radio never sees a control that does nothing.
Segmented {
    id: root
    readonly property var tak: Survival.tak
    objectName: "networkFilter"
    visible: tak.networks.length > 1
    accessibleName: qsTr("Show people and messages from")
    model: [{id: "", text: qsTr("All")}].concat(tak.networks.map(n => ({id: n.id, text: n.name})))
    currentId: tak.networkFilter
    onActivated: (id) => tak.networkFilter = id
}
