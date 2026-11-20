import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Apps 1.0

// A drop-down. `accessibleName` is spoken with the current choice.
C.ComboBox {
    id: root
    property string accessibleName: ""
    property bool invalid: false
    implicitHeight: 32
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Look.family
    font.pixelSize: Look.body
    Accessible.name: accessibleName
    Accessible.role: Accessible.ComboBox
    background: Rectangle {
        radius: Look.radiusControl
        color: root.hovered ? Look.hover : Look.field
        border.width: root.visualFocus || root.popup.visible ? 2 : 1
        border.color: root.invalid ? Look.danger : root.visualFocus || root.popup.visible ? Look.accent : Look.hairline
    }
    contentItem: TaosText {
        leftPadding: 10; rightPadding: 28
        verticalAlignment: Text.AlignVCenter
        text: root.displayText
        elide: Text.ElideRight
        color: root.currentIndex < 0 || root.currentValue === "" ? Look.textTertiary : Look.text
    }
    indicator: Glyph { x: root.width - width - 10; y: (root.height - height) / 2; name: "chevron-down"; size: 13; stroke: 2 }
    delegate: C.ItemDelegate {
        width: root.width - 8
        height: 30
        highlighted: root.highlightedIndex === index
        Accessible.name: model[root.textRole] !== undefined ? String(model[root.textRole]) : String(modelData)
        contentItem: TaosText {
            text: model[root.textRole] !== undefined ? model[root.textRole] : modelData
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { radius: 6; color: parent.highlighted ? Look.tint(Look.accent, 0.22) : "transparent" }
    }
    popup: C.Popup {
        y: root.height + 4
        width: root.width
        padding: 4
        implicitHeight: Math.min(contentItem.implicitHeight + 8, 240)
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
        }
        background: Rectangle { radius: 10; color: Look.dark ? "#1D1D22" : "#FAFAFC"; border.width: 1; border.color: Look.glassStroke }
    }
}
