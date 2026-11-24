#include "dockcli.h"

#include <QJsonDocument>
#include <QJsonObject>

#include "desktopentry.h"
#include "dockconfig.h"
#include "dockcontroller.h"

namespace DockCli {

bool isCommand(const QString &w) {
    return w == QLatin1String("get") || w == QLatin1String("set") || w == QLatin1String("pin") || w == QLatin1String("unpin") || w == QLatin1String("list");
}

static QString normId(QString id) {
    if (id.endsWith(QLatin1String(".desktop"))) id.chop(8);
    return id;
}

int run(const QStringList &a, const QString &configPath, QString *out, QString *err, const QStringList &appDirs) {
    const QString path = configPath.isEmpty() ? DockConfigFile::userPath() : configPath;
    DockConfig c;
    DockConfigFile::read(path, &c);   // missing file: defaults
    const QString cmd = a.value(0);
    auto fail = [&](const QString &m) { *err += m + QLatin1Char('\n'); return 1; };
    if (cmd == QLatin1String("get")) {
        if (a.size() >= 2) {
            bool ok = false;
            const QString v = DockConfigFile::getKey(c, a.at(1), &ok);
            if (!ok) return fail(QStringLiteral("taos-dock: unknown key '%1'").arg(a.at(1)));
            *out += v + QLatin1Char('\n');
            return 0;
        }
        for (const char *k : {"position", "attached", "autohide", "size", "magnify", "hideInTiling", "outputs"})
            *out += QStringLiteral("%1 %2\n").arg(QLatin1String(k), DockConfigFile::getKey(c, QLatin1String(k)));
        return 0;
    }
    if (cmd == QLatin1String("set")) {
        if (a.size() != 3) return fail(QStringLiteral("usage: taos-dock set <key> <value>"));
        QString e;
        if (!DockConfigFile::setKey(&c, a.at(1), a.at(2), &e)) return fail(QStringLiteral("taos-dock: ") + e);
        if (!DockConfigFile::write(path, c, &e)) return fail(QStringLiteral("taos-dock: cannot write %1: %2").arg(path, e));
        return 0;
    }
    DesktopIndex idx;
    idx.scan(appDirs.isEmpty() ? Desktop::defaultDirs() : appDirs);
    if (!c.pinnedSet) { c.pinned = DockController::defaultPinned(idx); c.pinnedSet = true; }
    if (cmd == QLatin1String("pin") || cmd == QLatin1String("unpin")) {
        if (a.size() != 2) return fail(QStringLiteral("usage: taos-dock %1 <desktop-id>").arg(cmd));
        const QString id = normId(a.at(1));
        if (cmd == QLatin1String("pin")) {
            if (!idx.byId(id)) return fail(QStringLiteral("taos-dock: no application '%1' is installed").arg(id));
            if (!c.pinned.contains(id)) c.pinned << id;
        } else {
            c.pinned.removeAll(id);
        }
        QString e;
        if (!DockConfigFile::write(path, c, &e)) return fail(QStringLiteral("taos-dock: cannot write %1: %2").arg(path, e));
        return 0;
    }
    if (cmd == QLatin1String("list")) {
        for (const QString &id : std::as_const(c.pinned)) {
            const DesktopEntry *e = idx.byId(id);
            *out += e ? QStringLiteral("%1\t%2\n").arg(id, e->name) : QStringLiteral("%1\t(not installed)\n").arg(id);
        }
        return 0;
    }
    return fail(QStringLiteral("usage: taos-dock [run] | get [key] | set <key> <value> | pin <id> | unpin <id> | list"));
}

}  // namespace DockCli
