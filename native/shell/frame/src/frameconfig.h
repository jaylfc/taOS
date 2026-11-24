// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QRegularExpression>
#include <QString>
#include <QStringList>
#include <QVector>

// Which windows get a controls strip: ~/.config/taos/frame.json
//   { "include": [".*"], "exclude": [], "selfDecorating": ["^org\\.gnome\\."], "dialogs": [], "fixedSize": [] }
// The rule is "every window except": a window is framed unless its app_id matches an exclude or selfDecorating
// pattern, or it is a taOS app (my.taos.*, which draws its own header) or a browser that draws its own frame, or it
// told sway it draws its own decoration (border csd; that is detected, not configured). `include` narrows the set
// when a person wants that (default: everything). `dialogs` and `fixedSize` name apps whose windows get close only
// or no full size button; Wayland does not tell sway which windows those are, so this is data.
struct FrameConfig {
    QStringList include{QStringLiteral(".*")};
    QStringList exclude;
    // Apps that insist on drawing their own header bar (GTK header bars, Firefox). The GTK ones show the same three
    // buttons in the same order and side through the button-layout the theme exports; they get no second set.
    QStringList selfDecorating = defaultSelfDecorating();
    QStringList dialogs;          // app ids whose windows are dialogs: close only
    QStringList fixedSize;        // app ids whose windows cannot be resized: no full size button
    bool frameAllForeign = false; // legacy: now the same as the default include

    static QStringList defaultSelfDecorating();
    // Never framed, whatever the file says: taOS apps and normal browser windows (they draw their own header).
    static QStringList builtinExclude();
    bool shouldFrame(const QString &appId) const;
    enum class Kind { Normal, Dialog, FixedSize };
    Kind kindOf(const QString &appId) const;
    bool operator==(const FrameConfig &o) const {
        return include == o.include && exclude == o.exclude && frameAllForeign == o.frameAllForeign
               && selfDecorating == o.selfDecorating && dialogs == o.dialogs && fixedSize == o.fixedSize;
    }
};

namespace FrameConfigFile {
QString userPath();   // $TAOS_FRAME_CONFIG, else $XDG_CONFIG_HOME/taos/frame.json, else ~/.config/taos/frame.json
// A missing file is the default config (true). A file that is not valid JSON leaves `out` at the default and
// returns false with the reason. Bad regular expressions are dropped and named in `error` (the rest still applies).
bool read(const QString &path, FrameConfig *out, QString *error = nullptr);
}
