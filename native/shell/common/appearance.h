// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QDateTime>
#include <QString>

// The per person appearance setting: ~/.config/taos/appearance.json
//   {"theme": "graphite", "mode": "dark" | "light" | "auto", "windowButtons": "left" | "right"}   (windowButtons optional)
struct Appearance {
    QString theme = QStringLiteral("graphite");
    QString mode = QStringLiteral("dark");
    QString windowButtons;   // "" (follow the theme), "left" or "right": the person's own choice of button side
    bool operator==(const Appearance &o) const { return theme == o.theme && mode == o.mode && windowButtons == o.windowButtons; }
    bool operator!=(const Appearance &o) const { return !(*this == o); }
};

namespace AppearanceFile {
QString userPath();        // $TAOS_APPEARANCE, else $XDG_CONFIG_HOME/taos/appearance.json, else ~/.config/taos/appearance.json
QString systemPath();      // $TAOS_SYSTEM_APPEARANCE, else /etc/taos/appearance.json
QString mirrorDir();       // $TAOS_APPEARANCE_MIRROR_DIR, else /var/lib/taos/appearance
bool validMode(const QString &m);
// Reads and sanitises: a bad theme id or mode becomes the default. False when the file is
// missing or unreadable or not a JSON object (the Appearance is then the default).
bool read(const QString &path, Appearance *out, QString *error = nullptr);
// Written atomically (temp file in the same directory, then rename); creates the directory.
bool write(const QString &path, const Appearance &a, QString *error = nullptr, bool worldReadable = false);

// "auto" follows a plain schedule for now: light from 07:00 to 19:00 local time, dark otherwise.
// TODO: follow sunrise and sunset (needs the location; the weather service already knows it).
constexpr int kAutoLightFromHour = 7;
constexpr int kAutoLightToHour = 19;
QString resolveVariant(const QString &mode, const QDateTime &now);
}  // namespace AppearanceFile
