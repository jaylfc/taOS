// SPDX-License-Identifier: AGPL-3.0-or-later
import QtQuick
import Taos.Launcher

// A quiet section title (Pinned, Recent, a category, a letter).
Text {
    color: Style.textTertiary
    font.family: Style.fontDataStack
    font.pixelSize: Style.fontSize - 2
    font.weight: Font.DemiBold
    font.letterSpacing: Style.upperTitles ? Math.max(0.6, Style.titleTracking * Style.fontSize) : 0.6
    font.capitalization: Font.AllUppercase
    Accessible.role: Accessible.Heading
}
