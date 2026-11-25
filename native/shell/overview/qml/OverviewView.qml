// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Overview

// Super tap: the desktops (spaces) as live miniatures in a strip on top, a search field, and the windows of the
// current desktop packed below without overlap. Windows can be dragged onto a desktop, desktops dragged to reorder,
// names edited in place. On a narrow or tall screen (a phone) the strip becomes a row of chips and the windows are
// big touch cards that close with a swipe up.
Item {
    id: ov
    property real progress: 0
    property bool settled: false
    readonly property string font: Style.fontStack
    readonly property real fs: Style.fontSize > 0 ? Style.fontSize : 14           // body size from the theme
    readonly property real u: 8                                                    // spacing grid
    readonly property bool compact: width < 760 || height > width * 1.1
    readonly property var spaces: Overview.workspaces
    readonly property int spaceCount: Math.max(0, spaces.length - 1)               // without the "new" tile

    // ---- geometry, all derived from the screen --------------------------------------------------
    readonly property real margin: Math.round(Math.max(2 * u, width * 0.03))
    readonly property real tileGap: 2 * u
    readonly property real tileH: compact ? 0 : Math.round(Math.max(96, Math.min(220, height * 0.17)))
    readonly property real outAspect: {
        const s = spaces.length > 0 ? spaces[0].size : Qt.size(16, 10);
        return s.height > 0 ? s.width / s.height : 1.6;
    }
    readonly property real newTileW: Math.round(tileH * 0.9)
    // one scale for every tile so a long row of desktops still fits on the screen
    readonly property real rowScale: {
        if (compact || spaceCount === 0) return 1;
        const want = spaceCount * tileH * outAspect + newTileW + spaceCount * tileGap;
        const room = width - 2 * margin - 4 * u;
        return Math.min(1, room / want);
    }
    readonly property real tH: Math.round(tileH * rowScale)
    readonly property real tW: Math.round(tileH * outAspect * rowScale)
    readonly property real nW: Math.round(newTileW * rowScale)
    readonly property real labelH: 3 * u
    readonly property real stripPad: 2 * u
    readonly property real rowW: spaceCount * tW + nW + spaceCount * tileGap
    readonly property real stripH: compact ? 7 * u : tH + labelH + 2 * stripPad
    readonly property real stripW: compact ? width - 2 * u : Math.min(width - 2 * u, rowW + 2 * stripPad + 2 * u)
    readonly property real stripY: compact ? 3 * u : 3 * u
    readonly property real searchH: 5 * u + 4
    readonly property real searchY: stripY + stripH + 2 * u
    readonly property real hintH: compact ? 0 : 5 * u
    readonly property real areaX: compact ? 2 * u : margin
    readonly property real areaY: searchY + searchH + 5 * u
    readonly property real areaW: width - 2 * areaX
    readonly property real areaH: Math.max(100, height - areaY - hintH - 4 * u)

    function dur(ms) { return Style.reduceMotion ? 0 : ms; }
    function pushArea() { Overview.setOverviewArea(Qt.rect(areaX, areaY, areaW, areaH)); }
    onAreaWChanged: pushArea()
    onAreaHChanged: pushArea()
    onAreaYChanged: pushArea()
    Component.onCompleted: { pushArea(); pushMini(); }
    onTHChanged: pushMini()
    function pushMini() {
        const h = compact ? 5 * u : Math.max(48, tH - 2 * u);
        Overview.setMiniSize(Qt.size(Math.round(h * outAspect), h));
    }
    onProgressChanged: if (progress <= 0.001) { settled = false; cancelEdit(); }
    Timer { running: ov.progress >= 0.999 && !ov.settled; interval: 60; onTriggered: ov.settled = true }

    // ---- strip geometry helpers (the strip is one mouse area, so rebuilding the model never drops a drag) ----
    readonly property real stripX: Math.round((width - stripW) / 2)
    readonly property real rowX: compact ? stripX + stripPad : Math.round(stripX + (stripW - rowW) / 2)
    function tileX(i) { return rowX + i * (tW + tileGap); }
    function chipW(i) { return compact ? Math.min(width * 0.4, chipLabel(i).length * 0.6 * fs + 5 * u) : tW; }
    function chipLabel(i) { return i < spaces.length ? spaces[i].label : ""; }
    // which tile is at a point in ov coordinates; -1 none; index == spaceCount means the "new" tile
    function tileAt(px, py) {
        if (compact) {
            const r = chipRow.mapFromItem(ov, px, py);
            for (let i = 0; i < chipRepeater.count; ++i) {
                const it = chipRepeater.itemAt(i);
                if (it && r.x >= it.x && r.x <= it.x + it.width && r.y >= 0 && r.y <= chipRow.height) return i;
            }
            return -1;
        }
        if (py < stripY || py > stripY + stripH - labelH) return -1;
        for (let i = 0; i <= spaceCount; ++i) {
            const w = i < spaceCount ? tW : nW;
            if (px >= tileX(i) && px <= tileX(i) + w && py >= stripY + stripPad && py <= stripY + stripPad + tH) return i;
        }
        return -1;
    }
    function insertionIndex(px) {
        let idx = 0;
        for (let i = 0; i < spaceCount; ++i) if (px > tileX(i) + tW / 2) idx = i + 1;
        return Math.min(idx, spaceCount - 1);
    }

    // ---- drag state (windows onto desktops, desktops into a new order) ------------------------------
    property int dragWin: 0                 // conId being dragged, 0 none
    property string dragSpace: ""           // key of the desktop being dragged
    property real dragX: 0
    property real dragY: 0
    property real dragOffX: 0
    property real dragOffY: 0
    property int dropIndex: -1              // tile under the pointer while dragging a window
    property int insertIndex: -1            // where a dragged desktop would land
    property var dragWinInfo: null
    readonly property bool dragging: dragWin !== 0 || dragSpace !== ""
    function endDrag() { dragWin = 0; dragSpace = ""; dropIndex = -1; insertIndex = -1; dragWinInfo = null; }

    // ---- renaming ---------------------------------------------------------------------------------
    property string editKey: ""
    function beginRename(key) {
        if (key === undefined || key === "") for (let i = 0; i < spaceCount; ++i) if (spaces[i].current) key = spaces[i].key;
        if (key === undefined || key === "") return;
        editKey = key;
        editField.forceActiveFocus();
    }
    function cancelEdit() { if (editKey !== "") { editKey = ""; ov.parent.forceActiveFocus(); } }
    function commitEdit() {
        if (editKey === "") return;
        Overview.renameSpace(editKey, editField.text);
        cancelEdit();
    }
    function indexOfKey(k) { for (let i = 0; i < spaceCount; ++i) if (spaces[i].key === k) return i; return -1; }

    // ================== the strip =======================================================================
    Item {
        id: strip
        x: ov.stripX; y: ov.stripY
        width: ov.stripW; height: ov.stripH
        opacity: ov.progress
        transform: Translate { y: (1 - ov.progress) * -14 }
        Glass { anchors.fill: parent; radius: Style.radius(ov.compact ? 14 : 20) }

        // ---- wide: live miniatures
        Repeater {
            id: tileRepeater
            model: ov.compact ? [] : ov.spaces
            delegate: Item {
                id: tile
                required property var modelData
                required property int index
                readonly property bool isNew: modelData.isNew
                readonly property bool cur: modelData.current
                readonly property bool hovered: ov.tileAt(hover.px + ov.stripX, hover.py + ov.stripY) === index && hover.inside
                readonly property bool dropHot: ov.dropIndex === index && ov.dragWin !== 0 && !isNew
                readonly property bool beingDragged: ov.dragSpace !== "" && ov.dragSpace === modelData.key
                x: ov ? ov.tileX(index) - strip.x : 0
                y: ov.stripPad
                width: isNew ? ov.nW : ov.tW
                height: ov.tH
                opacity: beingDragged ? 0.35 : 1
                Behavior on x { enabled: ov.settled; NumberAnimation { duration: ov.dur(180); easing.type: Easing.OutCubic } }
                Accessible.role: Accessible.Button
                Accessible.name: isNew ? qsTr("New desktop")
                                       : qsTr("%1, %2 windows").arg(modelData.label).arg(modelData.count)
                Accessible.onPressAction: isNew ? Overview.addSpace() : Overview.goToSpace(modelData.key)

                Rectangle {
                    anchors.fill: parent
                    radius: Style.radius(8)
                    color: tile.isNew ? "transparent" : Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, Style.dark ? 0.70 : 0.80)
                    border.width: tile.isNew ? 1 : 0
                    border.color: Style.glassStroke
                }
                Item {
                    id: shot
                    anchors.fill: parent
                    visible: !tile.isNew
                    scale: tile.hovered && !ov.dragging ? 1.03 : 1.0
                    Behavior on scale { NumberAnimation { duration: ov.dur(150); easing.type: Easing.OutCubic } }
                    Repeater {
                        model: tile.modelData.windows
                        delegate: WindowThumb {
                            required property var modelData
                            readonly property var ws: tile.modelData
                            x: modelData.rect.x / ws.size.width * shot.width
                            y: modelData.rect.y / ws.size.height * shot.height
                            width: modelData.rect.width / ws.size.width * shot.width
                            height: modelData.rect.height / ws.size.height * shot.height
                            identifier: modelData.identifier
                            iconSource: modelData.iconSource
                            aspect: modelData.rect.height > 0 ? modelData.rect.width / modelData.rect.height : 1.6
                            radius: 2
                        }
                    }
                    // an empty desktop says so
                    Text {
                        visible: tile.modelData.count === 0 && !tile.isNew
                        anchors.centerIn: parent
                        text: qsTr("Empty")
                        color: Style.textTertiary
                        font.family: ov.font
                        font.pixelSize: ov.fs - 2
                    }
                }
                Rectangle {
                    anchors.fill: parent
                    radius: Style.radius(8)
                    color: tile.dropHot ? Qt.rgba(Style.accent.r, Style.accent.g, Style.accent.b, 0.22) : "transparent"
                    border.width: tile.cur || tile.dropHot ? 2 : 1
                    border.color: tile.cur || tile.dropHot ? Style.accent : (tile.hovered && !tile.isNew ? Style.textSecondary : Style.glassStroke)
                    visible: !tile.isNew || tile.hovered
                }
                // plus for the "new desktop" tile
                Item {
                    visible: tile.isNew
                    anchors.centerIn: parent
                    width: 3 * ov.u; height: 3 * ov.u
                    opacity: tile.hovered ? 1 : 0.7
                    Rectangle { anchors.centerIn: parent; width: parent.width; height: 2; radius: 1; color: Style.text }
                    Rectangle { anchors.centerIn: parent; width: 2; height: parent.height; radius: 1; color: Style.text }
                }
                // remove button, on hover (and on the current desktop when there is more than one)
                Rectangle {
                    id: closeBtn
                    visible: !tile.isNew && tile.modelData.removable && (tile.hovered || tile.cur) && !ov.dragging
                    readonly property bool overBtn: hover.inside && hover.overClose === tile.index
                    width: 3 * ov.u; height: 3 * ov.u; radius: width / 2
                    x: parent.width - width - 4; y: 4
                    color: overBtn ? Style.danger : Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, 0.85)
                    border.width: 1; border.color: Style.glassStroke
                    Item {
                        anchors.centerIn: parent
                        width: 10; height: 10
                        Rectangle { anchors.centerIn: parent; width: 11; height: 2; radius: 1; rotation: 45; color: closeBtn.overBtn ? Style.accentText : Style.text }
                        Rectangle { anchors.centerIn: parent; width: 11; height: 2; radius: 1; rotation: -45; color: closeBtn.overBtn ? Style.accentText : Style.text }
                    }
                }
                // the name under the tile; looks like a field on hover so it reads as editable
                Rectangle {
                    id: nameBox
                    visible: !tile.isNew && ov.editKey !== tile.modelData.key
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: parent.height + ov.u / 2
                    width: Math.min(parent.width, nameText.implicitWidth + 3 * ov.u)
                    height: ov.labelH - ov.u / 2
                    radius: Style.radius(6)
                    color: tile.hovered ? Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.10) : "transparent"
                    Text {
                        id: nameText
                        anchors.centerIn: parent
                        width: Math.min(implicitWidth, parent.width - 2 * ov.u)
                        text: tile.modelData.label
                        color: tile.cur ? Style.text : Style.textSecondary
                        font.family: ov.font
                        font.pixelSize: ov.fs - 1
                        font.weight: tile.cur ? Font.DemiBold : Font.Medium
                        elide: Text.ElideRight
                        renderType: Text.NativeRendering
                    }
                }
                Text {
                    visible: tile.isNew
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: parent.height + ov.u / 2 + 2
                    text: qsTr("New desktop")
                    color: Style.textSecondary
                    font.family: ov.font
                    font.pixelSize: ov.fs - 1
                    font.weight: Font.Medium
                    renderType: Text.NativeRendering
                }
            }
        }

        // ---- compact: a row of chips
        Flickable {
            id: chipFlick
            visible: ov.compact
            anchors.fill: parent
            anchors.margins: ov.u
            contentWidth: chipRow.width
            contentHeight: height
            flickableDirection: Flickable.HorizontalFlick
            boundsBehavior: Flickable.StopAtBounds
            clip: true
            Row {
                id: chipRow
                height: parent.height
                spacing: ov.u
                Repeater {
                    id: chipRepeater
                    model: ov.compact ? ov.spaces : []
                    delegate: Rectangle {
                        required property var modelData
                        required property int index
                        height: chipRow.height
                        width: Math.max(6 * ov.u, chipText.implicitWidth + 4 * ov.u)
                        radius: height / 2
                        color: modelData.current ? Style.accent : Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.10)
                        border.width: ov.dropIndex === index ? 2 : 0
                        border.color: Style.accent
                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.isNew ? qsTr("New desktop") : modelData.label
                        Accessible.onPressAction: modelData.isNew ? Overview.addSpace() : Overview.goToSpace(modelData.key)
                        Text {
                            id: chipText
                            anchors.centerIn: parent
                            text: modelData.isNew ? "+" : modelData.label
                            color: modelData.current ? Style.accentText : Style.text
                            font.family: ov.font
                            font.pixelSize: ov.fs
                            font.weight: Font.Medium
                            renderType: Text.NativeRendering
                        }
                        MouseArea {
                            anchors.fill: parent
                            onClicked: modelData.isNew ? Overview.addSpace() : Overview.goToSpace(modelData.key)
                            onPressAndHold: if (!modelData.isNew) ov.beginRename(modelData.key)
                        }
                    }
                }
            }
        }

        // one mouse area for the wide strip: hover, click, drag to reorder, double-click to rename
        MouseArea {
            id: hover
            anchors.fill: parent
            enabled: !ov.compact
            hoverEnabled: true
            property bool inside: false
            property real px: 0
            property real py: 0
            property int overClose: -1
            property int pressIndex: -1
            property real pressX: 0
            property bool moved: false
            onEntered: inside = true
            onExited: { inside = false; overClose = -1; }
            function closeHit(i, mx, my) {
                if (i < 0 || i >= ov.spaceCount || !ov.spaces[i].removable) return false;
                const bx = ov.tileX(i) - strip.x + ov.tW - 3 * ov.u - 4, by = ov.stripPad + 4;
                return mx >= bx && mx <= bx + 3 * ov.u && my >= by && my <= by + 3 * ov.u;
            }
            function nameHit(i, mx, my) {
                return i >= 0 && i < ov.spaceCount && my > ov.stripPad + ov.tH && mx >= ov.tileX(i) - strip.x && mx <= ov.tileX(i) - strip.x + ov.tW;
            }
            function tileIndexAt(mx, my) {
                // tiles plus the label band under them
                for (let i = 0; i <= ov.spaceCount; ++i) {
                    const w = i < ov.spaceCount ? ov.tW : ov.nW;
                    const x = ov.tileX(i) - strip.x;
                    if (mx >= x && mx <= x + w && my >= ov.stripPad && my <= ov.stripPad + ov.tH + ov.labelH) return i;
                }
                return -1;
            }
            onPositionChanged: (m) => {
                px = m.x; py = m.y;
                const i = tileIndexAt(m.x, m.y);
                overClose = closeHit(i, m.x, m.y) ? i : -1;
                if (pressed && pressIndex >= 0 && pressIndex < ov.spaceCount) {
                    if (!moved && Math.abs(m.x - pressX) > 10) {
                        moved = true;
                        ov.dragSpace = ov.spaces[pressIndex].key;
                    }
                    if (moved) {
                        ov.dragX = m.x + ov.stripX; ov.dragY = m.y + ov.stripY;
                        ov.insertIndex = ov.insertionIndex(m.x + ov.stripX);
                    }
                }
            }
            onPressed: (m) => {
                Overview.touch();
                pressIndex = tileIndexAt(m.x, m.y); pressX = m.x; moved = false;
                if (pressIndex >= 0 && pressIndex < ov.spaceCount) {
                    const t = ov.spaces[pressIndex];
                    ov.dragOffX = m.x - (ov.tileX(pressIndex) - strip.x); ov.dragOffY = m.y - ov.stripPad;
                }
            }
            onReleased: (m) => {
                const i = pressIndex;
                const wasMoved = moved;
                pressIndex = -1; moved = false;
                if (wasMoved && ov.dragSpace !== "") {
                    const from = ov.indexOfKey(ov.dragSpace);
                    const to = ov.insertIndex;
                    ov.endDrag();
                    if (from >= 0 && to >= 0 && to !== from) Overview.moveSpace(ov.spaces[from].key, to);
                    return;
                }
                ov.endDrag();
                if (i < 0) return;
                if (i === ov.spaceCount) { Overview.addSpace(); return; }
                if (closeHit(i, m.x, m.y)) { Overview.removeSpace(ov.spaces[i].key); return; }
                if (nameHit(i, m.x, m.y) && ov.spaces[i].current) { ov.beginRename(ov.spaces[i].key); return; }
                Overview.goToSpace(ov.spaces[i].key);
            }
            onDoubleClicked: (m) => {
                const i = tileIndexAt(m.x, m.y);
                if (i >= 0 && i < ov.spaceCount) ov.beginRename(ov.spaces[i].key);
            }
        }
        // where a dragged desktop would land
        Rectangle {
            visible: ov.dragSpace !== "" && ov.insertIndex >= 0 && !ov.compact
            readonly property int from: ov.indexOfKey(ov.dragSpace)
            x: ov.tileX(ov.insertIndex > from ? ov.insertIndex + 1 : ov.insertIndex) - strip.x - ov.tileGap / 2 - 1
            y: ov.stripPad - 2; width: 3; height: ov.tH + 4; radius: 1.5
            color: Style.accent
        }
    }

    // the rename field sits where the name is
    Rectangle {
        id: editBox
        visible: ov.editKey !== ""
        readonly property int idx: ov.indexOfKey(ov.editKey)
        width: ov.compact ? 24 * ov.u : Math.max(ov.tW, 20 * ov.u)
        height: ov.labelH
        x: ov.compact ? Math.round((ov.width - width) / 2) : Math.round(ov.tileX(Math.max(0, idx)) + ov.tW / 2 - width / 2)
        y: ov.compact ? ov.stripY + ov.stripH + ov.u : ov.stripY + ov.stripPad + ov.tH + ov.u / 2 - 2
        radius: Style.radius(6)
        color: Style.surface
        border.width: 2
        border.color: Style.focusRing
        z: 20
        TextInput {
            id: editField
            anchors.fill: parent
            anchors.leftMargin: ov.u; anchors.rightMargin: ov.u
            verticalAlignment: TextInput.AlignVCenter
            horizontalAlignment: TextInput.AlignHCenter
            color: Style.text
            selectionColor: Style.selection
            selectedTextColor: Style.text
            font.family: ov.font
            font.pixelSize: ov.fs - 1
            maximumLength: 40
            clip: true
            Accessible.name: qsTr("Desktop name")
            onAccepted: ov.commitEdit()
            Keys.onEscapePressed: (e) => { ov.cancelEdit(); e.accepted = true; }
            onActiveFocusChanged: if (!activeFocus && ov.editKey !== "") ov.commitEdit()
        }
        onVisibleChanged: if (visible) {
            const i = idx;
            editField.text = i >= 0 ? ov.spaces[i].label : "";
            editField.selectAll();
            editField.forceActiveFocus();
        }
    }

    // ================== search ==================================================================================
    Item {
        id: search
        width: Math.min(ov.width - 4 * ov.u, 56 * ov.u)
        height: ov.searchH
        y: ov.searchY
        x: Math.round((ov.width - width) / 2)
        opacity: ov.progress
        Glass { anchors.fill: parent; radius: height / 2; shadow: false }
        // magnifier
        Item {
            x: 2 * ov.u; anchors.verticalCenter: parent.verticalCenter
            width: 2 * ov.u; height: 2 * ov.u
            opacity: 0.7
            Rectangle { width: 12; height: 12; radius: 6; color: "transparent"; border.width: 2; border.color: Style.textSecondary }
            Rectangle { x: 11; y: 13; width: 7; height: 2; radius: 1; rotation: 45; transformOrigin: Item.Left; color: Style.textSecondary }
        }
        Text {
            id: queryText
            anchors.verticalCenter: parent.verticalCenter
            x: 5 * ov.u
            width: parent.width - 10 * ov.u
            elide: Text.ElideLeft
            text: Overview.filterText.length > 0 ? Overview.filterText : qsTr("Search windows")
            color: Overview.filterText.length > 0 ? Style.text : Style.textSecondary
            font.family: ov.font
            font.pixelSize: ov.fs + 2
            renderType: Text.NativeRendering
            Accessible.role: Accessible.StaticText
            Accessible.name: Overview.filterText.length > 0 ? qsTr("Filter: %1").arg(Overview.filterText) : qsTr("Search windows")
        }
        Rectangle {
            id: caret
            visible: Overview.filterText.length > 0
            x: Math.min(queryText.x + queryText.implicitWidth + 2, queryText.x + queryText.width)
            anchors.verticalCenter: parent.verticalCenter
            width: 2; height: ov.fs + 4
            color: Style.accent
            SequentialAnimation on opacity {
                running: caret.visible && !Style.reduceMotion && ov.progress > 0.99
                loops: Animation.Infinite
                NumberAnimation { to: 0; duration: 500 }
                NumberAnimation { to: 1; duration: 500 }
            }
        }
        // clear
        Rectangle {
            visible: Overview.filterText.length > 0
            width: 3 * ov.u; height: 3 * ov.u; radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            x: parent.width - width - 2 * ov.u
            color: clearArea.containsMouse ? Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.2) : Qt.rgba(Style.text.r, Style.text.g, Style.text.b, 0.10)
            Accessible.role: Accessible.Button
            Accessible.name: qsTr("Clear search")
            Accessible.onPressAction: Overview.clearFilter()
            Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: 45; color: Style.text }
            Rectangle { anchors.centerIn: parent; width: 10; height: 2; radius: 1; rotation: -45; color: Style.text }
            MouseArea { id: clearArea; anchors.fill: parent; hoverEnabled: true; onClicked: Overview.clearFilter() }
        }
    }

    // ================== empty states ===========================================================================
    Column {
        visible: Overview.windowCount === 0 || (Overview.layout && Object.keys(Overview.layout).length === 0)
        anchors.horizontalCenter: parent.horizontalCenter
        y: ov.areaY + ov.areaH / 2 - height / 2
        spacing: ov.u
        opacity: ov.progress
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Overview.filterText.length > 0 ? qsTr("No windows match") : qsTr("No windows on this desktop")
            color: Style.textSecondary
            font.family: ov.font
            font.pixelSize: ov.fs + 6
            font.weight: Font.Medium
            renderType: Text.NativeRendering
        }
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: Overview.filterText.length > 0 ? qsTr("Press Escape to clear the search") : qsTr("Open an app from the menu (Super and Space)")
            color: Style.textTertiary
            font.family: ov.font
            font.pixelSize: ov.fs
            renderType: Text.NativeRendering
        }
    }

    // ================== the windows ============================================================================
    Item {
        id: field
        anchors.fill: parent
        opacity: ov.progress
        scale: 0.96 + 0.04 * ov.progress
        transformOrigin: Item.Center

        Repeater {
            model: Overview.windows
            delegate: Item {
                id: win
                required property int index
                required property int conId
                required property string title
                required property string appName
                required property string iconSource
                required property string identifier
                required property bool urgent
                required property bool minimised
                required property string accessibleText
                required property string workspace
                required property rect rect
                readonly property var lr: Overview.layout[conId]
                readonly property bool present: lr !== undefined
                readonly property bool selected: index === Overview.selectedIndex
                readonly property bool hot: (selected || winArea.containsMouse) && ov.dragWin !== conId
                readonly property bool lifted: ov.dragWin === conId
                x: present ? lr.x : 0
                y: present ? lr.y : 0
                width: present ? lr.width : 0
                height: present ? lr.height : 0
                visible: opacity > 0.01
                opacity: present ? (lifted ? 0.35 : 1) : 0
                z: hot ? 5 : 1
                enabled: present
                Behavior on x { enabled: ov.settled; NumberAnimation { duration: ov.dur(200); easing.type: Easing.OutCubic } }
                Behavior on y { enabled: ov.settled; NumberAnimation { duration: ov.dur(200); easing.type: Easing.OutCubic } }
                Behavior on width { enabled: ov.settled; NumberAnimation { duration: ov.dur(200); easing.type: Easing.OutCubic } }
                Behavior on height { enabled: ov.settled; NumberAnimation { duration: ov.dur(200); easing.type: Easing.OutCubic } }
                Behavior on opacity { NumberAnimation { duration: ov.dur(160) } }
                Accessible.role: Accessible.ListItem
                Accessible.name: accessibleText
                Accessible.selected: selected
                Accessible.focusable: true
                Accessible.onPressAction: Overview.focusWindow(conId)

                Item {
                    id: lift
                    anchors.fill: parent
                    scale: win.hot ? 1.03 : 1.0
                    opacity: win.minimised ? 0.55 : 1
                    transformOrigin: Item.Center
                    Behavior on scale { NumberAnimation { duration: ov.dur(150); easing.type: Easing.OutCubic } }
                    WindowThumb {
                        anchors.fill: parent
                        identifier: win.identifier
                        iconSource: win.iconSource
                        appName: win.appName
                        aspect: win.rect.height > 0 ? win.rect.width / win.rect.height : 1.6
                        radius: Style.radius(12)
                    }
                    Rectangle {
                        anchors.fill: parent
                        radius: Style.radius(12)
                        color: "transparent"
                        border.width: win.selected ? 3 : (winArea.containsMouse ? 2 : 0)
                        border.color: Style.accent
                        visible: border.width > 0
                    }
                    Rectangle {
                        visible: win.minimised
                        x: Math.round((parent.width - width) / 2); y: ov.u
                        width: minLabel.implicitWidth + 3 * ov.u; height: 3 * ov.u; radius: height / 2
                        color: Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, 0.82)
                        border.width: 1; border.color: Style.stroke
                        Text {
                            id: minLabel
                            anchors.centerIn: parent
                            text: qsTr("minimised")
                            color: Style.text
                            font.family: ov.font
                            font.pixelSize: ov.fs - 2
                            font.weight: Font.Medium
                            renderType: Text.NativeRendering
                        }
                    }
                    Rectangle {
                        visible: win.urgent
                        x: parent.width - 20; y: 8
                        width: 12; height: 12; radius: 6
                        color: Style.warning
                    }
                    // close, on hover or selection (a swipe up does the same on touch)
                    Rectangle {
                        id: wClose
                        visible: (win.hot || ov.compact) && !win.minimised
                        width: 3 * ov.u; height: 3 * ov.u; radius: width / 2
                        x: ov.u; y: ov.u
                        color: closeArea.containsMouse ? Style.danger : Qt.rgba(Style.ground.r, Style.ground.g, Style.ground.b, 0.85)
                        border.width: 1; border.color: Style.glassStroke
                        Accessible.role: Accessible.Button
                        Accessible.name: qsTr("Close %1").arg(win.appName)
                        Accessible.onPressAction: Overview.closeWindow(win.conId)
                        Rectangle { anchors.centerIn: parent; width: 11; height: 2; radius: 1; rotation: 45; color: closeArea.containsMouse ? Style.accentText : Style.text }
                        Rectangle { anchors.centerIn: parent; width: 11; height: 2; radius: 1; rotation: -45; color: closeArea.containsMouse ? Style.accentText : Style.text }
                        MouseArea { id: closeArea; anchors.fill: parent; anchors.margins: -4; hoverEnabled: true; z: 3; onClicked: Overview.closeWindow(win.conId) }
                    }
                }
                // app icon and title under the window, always shown
                Row {
                    id: caption
                    y: parent.height + ov.u
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: ov.u
                    width: Math.min(parent.width, iconSlot.width + captionText.implicitWidth + spacing)
                    AppBadge {
                        id: iconSlot
                        size: 3 * ov.u + 4
                        iconSource: win.iconSource
                        appName: win.appName
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        id: captionText
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(0, caption.width - iconSlot.width - caption.spacing)
                        elide: Text.ElideRight
                        text: win.title.length ? win.title : win.appName
                        color: Style.text
                        opacity: win.hot ? 1 : 0.85
                        font.family: ov.font
                        font.pixelSize: ov.fs
                        font.weight: win.selected ? Font.DemiBold : Font.Medium
                        renderType: Text.NativeRendering
                    }
                }
                MouseArea {
                    id: winArea
                    anchors.fill: parent
                    hoverEnabled: true
                    property real px: 0
                    property real py: 0
                    property bool dragged: false
                    onPositionChanged: (m) => {
                        if (!pressed) { Overview.selectedIndex = win.index; return; }
                        const p = mapToItem(ov, m.x, m.y);
                        if (!dragged && (Math.abs(p.x - px) > 12 || Math.abs(p.y - py) > 12)) {
                            dragged = true;
                            ov.dragWin = win.conId;
                            ov.dragWinInfo = { identifier: win.identifier, iconSource: win.iconSource, appName: win.appName, aspect: win.rect.height > 0 ? win.rect.width / win.rect.height : 1.6 };
                            ov.dragOffX = 0; ov.dragOffY = 0;
                        }
                        if (dragged) {
                            ov.dragX = p.x; ov.dragY = p.y;
                            ov.dropIndex = ov.tileAt(p.x, p.y);
                        }
                    }
                    onPressed: (m) => { Overview.touch(); const p = mapToItem(ov, m.x, m.y); px = p.x; py = p.y; dragged = false; }
                    onReleased: (m) => {
                        const wasDragged = dragged;
                        dragged = false;
                        const p = mapToItem(ov, m.x, m.y);
                        const target = ov.dropIndex;
                        ov.endDrag();
                        if (!wasDragged) return;
                        if (target >= 0 && target < ov.spaceCount && !ov.spaces[target].current) Overview.moveWindowToSpace(win.conId, ov.spaces[target].key);
                        else if (target === ov.spaceCount) {
                            const k = Overview.addSpace();
                            if (k !== "") Overview.moveWindowToSpace(win.conId, k);
                        } else if (ov.compact && (py - p.y) > 90 && Math.abs(p.x - px) < 60) Overview.closeWindow(win.conId);
                        suppress = true;
                    }
                    property bool suppress: false
                    onClicked: { if (suppress) { suppress = false; return; } Overview.focusWindow(win.conId); }
                }
            }
        }
    }

    // the window being dragged, following the pointer
    Item {
        id: ghost
        visible: ov.dragWin !== 0 && ov.dragWinInfo !== null
        z: 30
        width: ov.compact ? 20 * ov.u : 24 * ov.u
        height: width / (ov.dragWinInfo ? ov.dragWinInfo.aspect : 1.6)
        x: ov.dragX - width / 2
        y: ov.dragY - height / 2
        opacity: 0.92
        scale: 1.0
        WindowThumb {
            anchors.fill: parent
            identifier: ov.dragWinInfo ? ov.dragWinInfo.identifier : ""
            iconSource: ov.dragWinInfo ? ov.dragWinInfo.iconSource : ""
            appName: ov.dragWinInfo ? ov.dragWinInfo.appName : ""
            aspect: ov.dragWinInfo ? ov.dragWinInfo.aspect : 1.6
            radius: Style.radius(8)
        }
    }
    // a desktop being dragged to a new place
    Rectangle {
        visible: ov.dragSpace !== "" && !ov.compact
        z: 30
        width: ov.tW * 0.8; height: ov.tH * 0.8
        radius: Style.radius(8)
        x: ov.dragX - width / 2
        y: ov.stripY + ov.stripPad
        color: Qt.rgba(Style.accent.r, Style.accent.g, Style.accent.b, 0.25)
        border.width: 2; border.color: Style.accent
    }

    // ================== key hints ==============================================================================
    Text {
        visible: !ov.compact
        anchors.horizontalCenter: parent.horizontalCenter
        y: parent.height - ov.hintH + ov.u
        opacity: ov.progress * 0.85
        text: qsTr("Arrows select    Enter opens    1 to 9 jump to a desktop    Ctrl and arrows switch desktop    Esc closes")
        color: Style.textSecondary
        font.family: ov.font
        font.pixelSize: ov.fs - 2
        renderType: Text.NativeRendering
        Accessible.ignored: true
    }
}
