// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Dock

// The right-click menu and the window list: a clear overlay over the output (a click anywhere closes it) with a glass
// panel next to the item, and a further panel for a row that has a submenu (the controls of one window, the spaces to
// move it to). Keyboard: Up and Down move, Right (or Enter) opens a submenu, Left (or Escape) closes it, Enter runs the
// row, Escape closes the menu from the first panel. The pointer and the keys share one cursor, `cur`.
FocusScope {
    id: root
    property real anchorX: 0
    property real anchorY: 0
    property string edge: "bottom"
    property real clearance: Dock.iconSize / 2 + 24      // from the anchor to the panel; the taskbar passes a smaller one
    readonly property string fontFamily: "Inter, Noto Sans, DejaVu Sans, sans-serif"
    readonly property bool dark: Theme.variant !== "light"
    // The cursor: one row index per open panel; the last one is where the keyboard is (-1: no row yet in that panel).
    property var cur: [-1]
    focus: true
    Accessible.role: Accessible.Pane
    Accessible.name: "Dock menu"

    function rowsAt(level) {
        var r = Dock.menuRows
        for (var i = 0; i < level; ++i) r = r[cur[i]].children
        return r
    }
    function usable(r) { return !r.separator && (r.enabled || (r.children && r.children.length > 0)) }
    function first(rows) { for (var i = 0; i < rows.length; ++i) if (usable(rows[i])) return i; return -1 }
    function withLast(i) { var c = cur.slice(0, cur.length - 1); c.push(i); return c }
    function step(d) {
        var level = cur.length - 1
        var rows = rowsAt(level)
        var n = rows.length
        if (n === 0) return
        var at = cur[level]
        for (var i = 1; i <= n; ++i) {
            var k = ((at < 0 ? (d > 0 ? -1 : n) : at) + d * i + n * 4) % n
            if (usable(rows[k])) { cur = withLast(k); return }
        }
    }
    // The pointer is on a row: the cursor follows it, a submenu opens beside its row.
    function hover(level, index, hasChildren) {
        var c = cur.slice(0, level)
        c.push(index)
        if (hasChildren) c.push(-1)
        cur = c
    }
    function open(level, index) {
        var rows = rowsAt(level)
        var kids = rows[index].children
        var c = cur.slice(0, level)
        c.push(index)
        c.push(kids ? first(kids) : -1)
        cur = c
    }
    function activate() {
        var c = cur.slice(0)
        if (c.length > 1 && c[c.length - 1] < 0) c.pop()          // a submenu the pointer opened, no row chosen in it: its parent row
        var level = c.length - 1
        var i = c[level]
        if (i < 0) return
        var r = Dock.menuRows
        for (var k = 0; k < level; ++k) r = r[c[k]].children
        r = r[i]
        if (r.opens) { open(level, i); return }
        Dock.invokePath(c)
    }
    Keys.onEscapePressed: { if (cur.length > 1) cur = cur.slice(0, cur.length - 1); else Dock.closeMenu() }
    Keys.onDownPressed: step(1)
    Keys.onUpPressed: step(-1)
    Keys.onRightPressed: {
        var level = cur.length - 1
        var i = cur[level]
        if (i >= 0) { var r = rowsAt(level)[i]; if (r.children && r.children.length > 0) open(level, i) }
    }
    Keys.onLeftPressed: { if (cur.length > 1) cur = cur.slice(0, cur.length - 1) }
    Keys.onReturnPressed: activate()
    Keys.onEnterPressed: activate()
    Component.onCompleted: { forceActiveFocus(); if (!Theme.reduceMotion) openAnim.start(); else panel0.opacity = 1 }

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.AllButtons
        onPressed: Dock.closeMenu()
        Accessible.ignored: true
    }

    MenuPanel {
        id: panel0
        menu: root
        rows: Dock.menuRows
        path: []
        opacity: 0
        x: root.edge === "left" ? root.anchorX + Dock.iconSize / 2 + 18
         : root.edge === "right" ? root.anchorX - width - Dock.iconSize / 2 - 18
         : Math.max(8, Math.min(root.width - width - 8, root.anchorX - width / 2))
        y: root.edge === "bottom" ? root.anchorY - root.clearance - height
         : root.edge === "top" ? root.anchorY + root.clearance
         : Math.max(8, Math.min(root.height - height - 8, root.anchorY - height / 2))
        placeAbsX: x
        placeAbsY: y
        NumberAnimation { id: openAnim; target: panel0; property: "opacity"; from: 0; to: 1; duration: 130; easing.type: Easing.OutCubic }
    }
}
