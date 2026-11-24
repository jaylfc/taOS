import QtQuick
import QtQuick.Controls.Basic as C
import Taos.Apps 1.0

// A single-line field. `accessibleName` is what a screen reader says.
C.TextField {
    id: root
    property string accessibleName: placeholderText
    implicitHeight: 32
    font.family: Look.family
    font.pixelSize: Look.body
    color: Look.text
    placeholderTextColor: Look.textTertiary
    selectionColor: Look.selection
    selectedTextColor: Look.text
    leftPadding: 10; rightPadding: 10
    verticalAlignment: TextInput.AlignVCenter
    Accessible.name: accessibleName
    Accessible.role: Accessible.EditableText
    background: Rectangle {
        radius: Look.radiusControl
        color: Look.field
        border.width: root.activeFocus ? 2 : 1
        border.color: root.activeFocus ? Look.focusRing : Look.hairline
        Behavior on border.color { ColorAnimation { duration: Look.fast } }
    }
}
