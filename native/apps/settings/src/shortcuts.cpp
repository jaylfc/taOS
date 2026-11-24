#include "shortcuts.h"

#include <QObject>
#include <QVariantMap>

using namespace Qt::StringLiterals;

namespace TaosSettings {

QVariantList shellShortcuts()
{
    struct Row { const char *keys; const char *what; };
    static const Row rows[] = {
        {"Super + Tab", QT_TR_NOOP("Show all windows and workspaces")},
        {"Alt + Tab", QT_TR_NOOP("Switch to the next window")},
        {"Alt + Shift + Tab", QT_TR_NOOP("Switch to the previous window")},
        {"Super + M", QT_TR_NOOP("Minimise or restore the window")},
        {"Super + Shift + M", QT_TR_NOOP("Restore the last minimised window")},
        {"Super + Up", QT_TR_NOOP("Make the window full size")},
        {"Super + Shift + Up", QT_TR_NOOP("Make the window fullscreen")},
        {"Super + Shift + T", QT_TR_NOOP("Switch this workspace between desktop and tiling")},
        {"Volume keys", QT_TR_NOOP("Louder, quieter and mute")},
        {"Microphone key", QT_TR_NOOP("Mute the microphone")},
    };
    QVariantList out;
    for (const Row &r : rows)
        out << QVariantMap{{u"keys"_s, QString::fromUtf8(r.keys)}, {u"action"_s, QObject::tr(r.what)}};
    return out;
}

} // namespace TaosSettings
