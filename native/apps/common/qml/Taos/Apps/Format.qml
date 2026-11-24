// SPDX-License-Identifier: AGPL-3.0-or-later
pragma Singleton
import QtQuick
import "Logic.js" as Logic

// Formatting shared by the apps. `tick` changes every 30 s so bindings that call
// relativeTime(ts, Format.tick) stay current without a timer per row.
QtObject {
    id: root
    property real tick: Date.now()
    property Timer clock: Timer { interval: 30000; running: true; repeat: true; onTriggered: root.tick = Date.now() }
    function relativeTime(ts, now) { return Logic.relativeTime(ts, now === undefined ? Date.now() : now) }
    function toMillis(ts) { return Logic.toMillis(ts) }
}
