// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import QtQuick.Controls.Basic as C
import QtQuick.Layouts
import Taos.Apps 1.0
import Taos.Survival 1.0

// The map: the painted tiles, pan and zoom by pointer, touch and keys, a place search, markers, the person's own
// position and the attribution. Used by Map and by taOStak, which is the same map with more on it.
FocusScope {
    id: stage
    objectName: "mapStage"
    activeFocusOnTab: true
    property bool narrow: false
    property bool takMode: false
    property bool pickingMe: false          // the next tap sets "my position"
    readonly property var engine: Survival.map
    readonly property alias view: mv
    property string selectedMarker: ""
    property string selectedPerson: ""
    signal personClicked(string node)
    property var pick: null                 // {lon, lat, x, y} of the last tap on empty map
    function focusSearch() { searchField.forceActiveFocus() }
    function setSearch(t) { searchField.text = t }
    function flyTo(lon, lat, zoom) { mv.flyTo(lon, lat, zoom) }
    function centerOn(lon, lat) { mv.flyTo(lon, lat, Math.max(mv.zoom, 9)) }

    // The map is drawn in the theme: land and water from the ground, ink from the text, one accent for what the
    // person put on it. Every value here is a token or a mix of tokens.
    function mix(a, b, t) { return Qt.tint(a, Look.tint(b, t)) }
    readonly property var mapPalette: ({
        ground: Look.ground,
        earth: mix(Look.ground, Look.text, Look.dark ? 0.10 : 0.05),
        water: Look.dark ? mix(Look.ground, Look.text, 0.0) : mix(Look.ground, Look.text, 0.16),
        urban: mix(Look.ground, Look.text, Look.dark ? 0.145 : 0.09),
        green: mix(Look.ground, Look.success, Look.dark ? 0.17 : 0.2),
        building: mix(Look.ground, Look.text, Look.dark ? 0.16 : 0.12),
        roadMinor: mix(Look.ground, Look.text, Look.dark ? 0.22 : 0.20),
        roadMedium: mix(Look.ground, Look.text, Look.dark ? 0.30 : 0.28),
        roadMajor: mix(Look.ground, Look.text, Look.dark ? 0.42 : 0.40),
        roadHighway: mix(Look.ground, Look.text, Look.dark ? 0.60 : 0.55),
        path: mix(Look.ground, Look.text, 0.25),
        rail: mix(Look.ground, Look.text, 0.30),
        border: mix(Look.ground, Look.text, 0.38),
        text: Look.text,
        textSecondary: Look.textSecondary,
        halo: Look.ground,
        accent: Look.accent,
        onAccent: Look.accentText,
        you: Look.live
    })

    MapView {
        id: mv
        objectName: "mapView"
        anchors.fill: parent
        visible: stage.engine.loaded
        engine: stage.engine
        mapPalette: stage.mapPalette
        fontFamily: Look.family
        labelSize: Look.small
        reduceMotion: Look.reduceMotion
        selectedMarker: stage.selectedMarker
        Accessible.role: Accessible.Graphic
        Accessible.name: qsTr("Map of %1").arg(stage.engine.name)
    }

    // input
    MouseArea {
        id: pointer
        anchors.fill: parent
        enabled: stage.engine.loaded
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: pressed ? Qt.ClosedHandCursor : stage.pickingMe ? Qt.CrossCursor : Qt.OpenHandCursor
        property real lastX: 0
        property real lastY: 0
        property real startX: 0
        property real startY: 0
        property bool moved: false
        onPressed: (m) => { lastX = m.x; lastY = m.y; startX = m.x; startY = m.y; moved = false; stage.forceActiveFocus() }
        onPositionChanged: (m) => {
            const ll = mv.lonLatAt(m.x, m.y)
            hover.text = stage.engine.formatCoords(ll.lon, ll.lat)
            if (!pressed) return
            if (Math.abs(m.x - startX) + Math.abs(m.y - startY) > 6) moved = true
            if (moved) mv.panBy(m.x - lastX, m.y - lastY)
            lastX = m.x; lastY = m.y
        }
        onClicked: (m) => {
            if (moved) return
            const id = mv.markerAt(m.x, m.y)
            if (id !== "") { stage.selectedMarker = id; stage.pick = null; return }
            stage.selectedMarker = ""
            const ll = mv.lonLatAt(m.x, m.y)
            if (stage.pickingMe) { stage.engine.setMe(ll.lon, ll.lat); stage.pickingMe = false; return }
            stage.pick = { lon: ll.lon, lat: ll.lat, x: m.x, y: m.y }
        }
        onDoubleClicked: (m) => mv.zoomAt(m.x, m.y, 1)
        onWheel: (w) => mv.zoomAt(w.x, w.y, w.angleDelta.y / 240)
    }
    PinchHandler {
        id: pinch
        target: null
        enabled: stage.engine.loaded
        property real last: 1
        onActiveChanged: last = 1
        onScaleChanged: if (active) { mv.zoomAt(centroid.position.x, centroid.position.y, Math.log(scale / last) / Math.LN2); last = scale }
    }
    Keys.onPressed: (e) => {
        if (!stage.engine.loaded) return
        const step = 90
        if (e.key === Qt.Key_Left) mv.panBy(step, 0)
        else if (e.key === Qt.Key_Right) mv.panBy(-step, 0)
        else if (e.key === Qt.Key_Up) mv.panBy(0, step)
        else if (e.key === Qt.Key_Down) mv.panBy(0, -step)
        else if (e.key === Qt.Key_Plus || e.key === Qt.Key_Equal) mv.zoomAt(mv.width / 2, mv.height / 2, 1)
        else if (e.key === Qt.Key_Minus) mv.zoomAt(mv.width / 2, mv.height / 2, -1)
        else if (e.key === Qt.Key_Home) mv.resetView()
        else if (e.key === Qt.Key_M) stage.dropMarkerAtCenter()
        else if (e.key === Qt.Key_Escape) { stage.pick = null; stage.selectedMarker = ""; stage.pickingMe = false }
        else return
        e.accepted = true
    }
    function dropMarkerAtCenter() {
        const id = engine.addMarker(qsTr("Marker"), "", mv.centerLon, mv.centerLat)
        selectedMarker = id
    }

    // top-left: place search
    ColumnLayout {
        visible: stage.engine.loaded
        anchors { left: parent.left; top: parent.top; margins: Look.space3 }
        width: Math.min(360, parent.width - Look.space3 * 2 - (stage.narrow ? 0 : 56))
        spacing: Look.space1
        GlassPanel {
            strong: true
            Layout.fillWidth: true
            implicitHeight: 40
            radius: Look.radiusCard - 2
            TaosField {
                id: searchField
                objectName: "mapSearch"
                anchors { fill: parent; margins: 3 }
                leftPadding: 34
                background: Item {}
                placeholderText: stage.engine.indexReady ? qsTr("Search places") : qsTr("Indexing place names...")
                accessibleName: qsTr("Search places on the map")
                // searches again when the place index finishes building
                readonly property var results: { stage.engine.indexReady; return text.trim() === "" ? [] : stage.engine.searchPlaces(text) }
                Keys.onDownPressed: if (resultList.count > 0) { resultList.forceActiveFocus(); resultList.currentIndex = 0 }
                Keys.onEscapePressed: { text = ""; stage.forceActiveFocus() }
                onAccepted: if (results.length > 0) { stage.centerOn(results[0].lon, results[0].lat); text = ""; stage.forceActiveFocus() }
                Glyph { x: 10; anchors.verticalCenter: parent.verticalCenter; name: "search"; size: 15; stroke: 2; color: Look.textTertiary }
            }
        }
        GlassPanel {
            strong: true
            visible: searchField.text.trim() !== ""
            Layout.fillWidth: true
            implicitHeight: Math.min(264, Math.max(64, resultList.contentHeight + 12))
            radius: Look.radiusCard - 2
            ListView {
                id: resultList
                anchors { fill: parent; margins: 6 }
                clip: true
                model: searchField.results
                boundsBehavior: Flickable.StopAtBounds
                delegate: ListRow {
                    required property var modelData
                    width: ListView.view.width
                    implicitHeight: 44
                    glyph: "map-pin"
                    title: modelData.name
                    subtitle: modelData.kind
                    onClicked: { stage.centerOn(modelData.lon, modelData.lat); searchField.text = ""; stage.forceActiveFocus() }
                    Keys.onUpPressed: if (ListView.view.currentIndex === 0) searchField.forceActiveFocus(); else ListView.view.decrementCurrentIndex()
                    Keys.onDownPressed: ListView.view.incrementCurrentIndex()
                }
                TaosText {
                    visible: resultList.count === 0
                    anchors.centerIn: parent
                    text: stage.engine.indexReady ? qsTr("No place named like that") : qsTr("Still indexing place names")
                    font.pixelSize: Look.body; color: Look.textSecondary
                }
            }
        }
    }

    // top-right: zoom
    ColumnLayout {
        visible: stage.engine.loaded
        anchors { right: parent.right; top: parent.top; margins: Look.space3 }
        spacing: Look.space2
        GlassPanel {
            strong: true
            implicitWidth: 40; implicitHeight: 80; radius: Look.radiusCard - 2
            ColumnLayout {
                anchors.fill: parent
                spacing: 0
                TaosButton { Layout.fillWidth: true; Layout.fillHeight: true; glyph: "plus"; variant: "ghost"; accessibleName: qsTr("Zoom in")
                             onClicked: mv.zoomAt(mv.width / 2, mv.height / 2, 1) }
                TaosButton { Layout.fillWidth: true; Layout.fillHeight: true; glyph: "minus"; variant: "ghost"; accessibleName: qsTr("Zoom out")
                             onClicked: mv.zoomAt(mv.width / 2, mv.height / 2, -1) }
            }
        }
        GlassPanel {
            strong: true
            implicitWidth: 40; implicitHeight: 40; radius: Look.radiusCard - 2
            TaosButton { anchors.fill: parent; glyph: "compass"; variant: "ghost"; accessibleName: qsTr("Show the whole map pack"); onClicked: mv.resetView() }
        }
        GlassPanel {
            strong: true
            visible: stage.takMode && stage.engine.hasMe
            implicitWidth: 40; implicitHeight: 40; radius: Look.radiusCard - 2
            TaosButton { anchors.fill: parent; glyph: "locate"; variant: "ghost"; accessibleName: qsTr("Centre on my position")
                         onClicked: mv.flyTo(stage.engine.meLon, stage.engine.meLat, Math.max(mv.zoom, 10)) }
        }
    }


    // people on the map: each identity's last known position, with how long ago; tap one for their trail and messages
    property int viewTick: 0
    Connections { target: mv; function onViewChanged() { stage.viewTick++ } }
    Repeater {
        model: stage.takMode && stage.engine.loaded ? Survival.tak.places : []
        delegate: Item {
            id: who
            required property var modelData
            objectName: "mapPerson-" + modelData.node
            readonly property var sp: { stage.viewTick; return mv.screenPos(modelData.lon, modelData.lat) }
            readonly property bool inView: sp.x >= -20 && sp.x <= stage.width + 20 && sp.y >= -20 && sp.y <= stage.height + 20
            visible: inView
            x: sp.x - width / 2
            y: sp.y - 11
            width: tag.width
            height: 22
            Accessible.role: Accessible.Button
            Accessible.name: qsTr("%1, %2").arg(modelData.name).arg(modelData.positionAgo)
            Accessible.onPressAction: stage.personClicked(modelData.node)
            Rectangle {
                id: tag
                height: 22; width: lab.visible ? lab.implicitWidth + 28 : 22; radius: 11
                color: Look.tint(Look.ground, 0.9)
                border.width: 1; border.color: Look.tint(Look.accent, 0.6)
                Rectangle { x: 6; anchors.verticalCenter: parent.verticalCenter; width: 10; height: 10; radius: 5; color: Look.accent }
                TaosText { id: lab; x: 22; anchors.verticalCenter: parent.verticalCenter; font.pixelSize: Look.caption; font.weight: Font.Medium
                           visible: mv.zoom >= 8 || stage.selectedPerson === who.modelData.node
                           text: who.modelData.name + "  " + who.modelData.positionAgo }
            }
            TapHandler { onTapped: stage.personClicked(who.modelData.node) }
        }
    }

    // tap on empty map: what can be done here
    GlassPanel {
        id: pickCard
        visible: stage.pick !== null && !stage.pickingMe
        strong: true
        radius: Look.radiusCard - 2
        width: 244
        implicitHeight: pickCol.implicitHeight + Look.space3 * 2
        x: stage.pick ? Math.max(Look.space2, Math.min(stage.width - width - Look.space2, stage.pick.x - width / 2)) : 0
        y: stage.pick ? Math.max(Look.space2, Math.min(stage.height - height - Look.space2, stage.pick.y + 14)) : 0
        ColumnLayout {
            id: pickCol
            anchors { fill: parent; margins: Look.space3 }
            spacing: Look.space2
            TaosText { text: stage.pick ? stage.engine.formatCoords(stage.pick.lon, stage.pick.lat) : ""; font.pixelSize: Look.small
                       color: Look.textSecondary; font.features: { "tnum": 1 }
                       Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: text }
            PillButton { Layout.fillWidth: true; text: qsTr("Drop a marker here"); glyph: "map-pin"; variant: "primary"
                         onClicked: { stage.selectedMarker = stage.engine.addMarker(qsTr("Marker"), "", stage.pick.lon, stage.pick.lat); stage.pick = null } }
            PillButton { Layout.fillWidth: true; text: qsTr("I am here"); glyph: "user"
                         onClicked: { stage.engine.setMe(stage.pick.lon, stage.pick.lat); stage.pick = null } }
            PillButton { Layout.fillWidth: true; text: qsTr("Close"); variant: "outline"; onClicked: stage.pick = null }
        }
    }
    // a selected marker
    GlassPanel {
        id: markerCard
        readonly property var m: { stage.engine.markers; for (const k of stage.engine.markers) if (k.id === stage.selectedMarker) return k; return null }
        visible: m !== null
        strong: true
        radius: Look.radiusCard - 2
        width: 260
        implicitHeight: markerCol.implicitHeight + Look.space3 * 2
        anchors { horizontalCenter: stage.narrow ? parent.horizontalCenter : undefined; bottom: parent.bottom; bottomMargin: Look.space6 + 8 }
        x: stage.narrow ? 0 : Look.space3
        ColumnLayout {
            id: markerCol
            anchors { fill: parent; margins: Look.space3 }
            spacing: Look.space2
            TaosField {
                id: nameEdit
                Layout.fillWidth: true
                text: markerCard.m ? markerCard.m.name : ""
                accessibleName: qsTr("Marker name")
                onEditingFinished: if (markerCard.m && text !== markerCard.m.name) {
                    const old = markerCard.m
                    stage.engine.removeMarker(old.id)
                    stage.selectedMarker = stage.engine.addMarker(text, old.note, old.lon, old.lat)
                }
            }
            TaosText { text: markerCard.m ? markerCard.m.coords : ""; font.pixelSize: Look.small; color: Look.textSecondary; font.features: { "tnum": 1 } }
            RowLayout {
                Layout.fillWidth: true
                spacing: Look.space2
                PillButton { Layout.fillWidth: true; text: qsTr("Delete"); glyph: "trash"; onClicked: { stage.engine.removeMarker(stage.selectedMarker); stage.selectedMarker = "" } }
                PillButton { Layout.fillWidth: true; text: qsTr("Close"); variant: "outline"; onClicked: stage.selectedMarker = "" }
            }
        }
    }

    // picking "my position"
    GlassPanel {
        visible: stage.pickingMe
        strong: true
        radius: height / 2
        implicitWidth: pickRow.implicitWidth + Look.space5
        implicitHeight: 40
        anchors { horizontalCenter: parent.horizontalCenter; top: parent.top; topMargin: Look.space3 + (stage.narrow ? 48 : 0) }
        RowLayout {
            id: pickRow
            anchors.centerIn: parent
            spacing: Look.space3
            TaosText { text: qsTr("Tap the map where you are"); font.pixelSize: Look.body; font.weight: Font.Medium }
            TaosButton { text: qsTr("Cancel"); compact: true; variant: "ghost"; onClicked: stage.pickingMe = false }
        }
    }

    // bottom: scale, pointer position, attribution
    RowLayout {
        visible: stage.engine.loaded
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom; margins: Look.space3 }
        spacing: Look.space3
        Item {
            id: scaleBar
            readonly property real mpp: mv.metersPerPixel
            readonly property var nice: { const t = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000]
                                           let best = t[0]; for (const v of t) if (v / mpp <= 110) best = v; return best }
            Layout.preferredWidth: nice / Math.max(mpp, 1e-6) + 2
            Layout.preferredHeight: 30
            Layout.alignment: Qt.AlignBottom
            Rectangle { x: 0; y: 6; width: 2; height: 10; color: Look.text; opacity: 0.8 }
            Rectangle { x: 0; y: 14; width: parent.width; height: 2; color: Look.text; opacity: 0.8 }
            Rectangle { x: parent.width - 2; y: 6; width: 2; height: 10; color: Look.text; opacity: 0.8 }
            TaosText { y: 16; x: 0; font.pixelSize: Look.caption; font.weight: Font.Medium
                       text: scaleBar.nice >= 1000 ? qsTr("%1 km").arg(scaleBar.nice / 1000) : qsTr("%1 m").arg(scaleBar.nice)
                       style: Text.Outline; styleColor: Look.ground }
        }
        TaosText {
            id: hover
            visible: !stage.narrow
            Layout.alignment: Qt.AlignBottom
            font.pixelSize: Look.caption; color: Look.textSecondary; font.features: { "tnum": 1 }
            style: Text.Outline; styleColor: Look.ground
        }
        Item { Layout.fillWidth: true }
        TaosText {
            Layout.alignment: Qt.AlignBottom
            Layout.maximumWidth: stage.width * (stage.narrow ? 0.6 : 0.45)
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideLeft
            text: stage.engine.attribution
            font.pixelSize: Look.caption; color: Look.textSecondary
            style: Text.Outline; styleColor: Look.ground
            Accessible.ignored: false; Accessible.role: Accessible.StaticText; Accessible.name: qsTr("Map data: %1").arg(text)
        }
    }
}
