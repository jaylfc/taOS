// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Apps 1.0

// A small upper-case label over a group of rows in the taOStak panels.
TaosText {
    font.pixelSize: Look.caption
    font.weight: Font.DemiBold
    font.capitalization: Font.AllUppercase
    font.letterSpacing: 0.6
    color: Look.textTertiary
}
