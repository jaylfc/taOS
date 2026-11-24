// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// Text in the taOS face and colours. Decorative by default for assistive tech
// (the parent control carries the accessible name).
Text {
    font.family: Look.family
    font.pixelSize: Look.body
    color: Look.text
    elide: Text.ElideNone
    wrapMode: Text.NoWrap
    renderType: Text.NativeRendering
    Accessible.ignored: true
}
