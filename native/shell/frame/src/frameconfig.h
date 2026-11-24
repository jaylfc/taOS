#pragma once
#include <QRegularExpression>
#include <QString>
#include <QStringList>
#include <QVector>

// Which windows get a controls strip: ~/.config/taos/frame.json
//   { "include": ["^chrome-"], "exclude": ["^chromium$"], "frameAllForeign": false }
// A window is framed when its app_id matches an include pattern (or frameAllForeign is on), matches no exclude
// pattern, and is not a taOS app (my.taos.*, which draws its own header) nor a browser that draws its own frame.
struct FrameConfig {
    QStringList include{QStringLiteral("^chrome-")};
    QStringList exclude;
    bool frameAllForeign = false;

    // Never framed, whatever the file says: taOS apps and normal browser windows (they draw their own header).
    static QStringList builtinExclude();
    bool shouldFrame(const QString &appId) const;
    bool operator==(const FrameConfig &o) const {
        return include == o.include && exclude == o.exclude && frameAllForeign == o.frameAllForeign;
    }
};

namespace FrameConfigFile {
QString userPath();   // $TAOS_FRAME_CONFIG, else $XDG_CONFIG_HOME/taos/frame.json, else ~/.config/taos/frame.json
// A missing file is the default config (true). A file that is not valid JSON leaves `out` at the default and
// returns false with the reason. Bad regular expressions are dropped and named in `error` (the rest still applies).
bool read(const QString &path, FrameConfig *out, QString *error = nullptr);
}
