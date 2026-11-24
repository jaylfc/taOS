// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick

// Holds the last list it was given and changes `value` only when the content differs. The engine hands out a fresh
// array on every refresh and install poll; a Repeater over such an array rebuilds every delegate and the keyboard
// focus on one of them is lost. Bind a view to `value` instead of the source.
QtObject {
    id: root
    property var source: []
    property var value: []
    property string key: "[]"
    function sync() {
        const k = JSON.stringify(root.source)
        if (k === root.key) return
        root.key = k
        root.value = root.source
    }
    onSourceChanged: sync()
    Component.onCompleted: sync()
}
