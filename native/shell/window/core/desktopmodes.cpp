// SPDX-License-Identifier: AGPL-3.0-or-later
#include "desktopmodes.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <algorithm>

#include "windowops.h"

namespace TaosWin {

namespace Modes {
bool valid(const QString &m) { return m == QLatin1String("desktop") || m == QLatin1String("tiling"); }

QString path() {
    const QString e = qEnvironmentVariable("TAOS_DESKTOP_CONFIG");
    if (!e.isEmpty()) return e;
    const QString xdg = qEnvironmentVariable("XDG_CONFIG_HOME");
    return (xdg.isEmpty() ? QDir::homePath() + QStringLiteral("/.config") : xdg) + QStringLiteral("/taos/desktop.json");
}

bool read(const QString &p, DesktopModes *out, QString *error) {
    *out = DesktopModes();
    QFile f(p);
    if (!f.exists()) return true;
    if (!f.open(QIODevice::ReadOnly)) { if (error) *error = QStringLiteral("cannot read %1").arg(p); return false; }
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.read(256 * 1024), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = QStringLiteral("%1 is not a JSON object").arg(p);
        return false;
    }
    const QJsonObject o = d.object();
    const QString def = o.value(QLatin1String("default")).toString();
    if (valid(def)) out->defaultMode = def;
    const QJsonObject ws = o.value(QLatin1String("workspaces")).toObject();
    for (auto it = ws.begin(); it != ws.end(); ++it)
        if (valid(it.value().toString())) out->workspaces.insert(it.key(), it.value().toString());
    return true;
}

bool write(const QString &p, const DesktopModes &m, QString *error) {
    if (!QDir().mkpath(QFileInfo(p).absolutePath())) { if (error) *error = QStringLiteral("cannot create the folder for %1").arg(p); return false; }
    QSaveFile f(p);
    f.setDirectWriteFallback(false);
    if (!f.open(QIODevice::WriteOnly)) { if (error) *error = QStringLiteral("cannot write %1").arg(p); return false; }
    QJsonObject ws;
    for (auto it = m.workspaces.begin(); it != m.workspaces.end(); ++it) ws.insert(it.key(), it.value());
    QJsonObject o;
    o.insert(QStringLiteral("default"), m.defaultMode);
    o.insert(QStringLiteral("workspaces"), ws);
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    if (!f.commit()) { if (error) *error = QStringLiteral("cannot save %1").arg(p); return false; }
    return true;
}
}  // namespace Modes

static QString at(int id) { return Plans::sel(id); }

QStringList modeSwitchCommands(const Tree &t, const QString &workspace, const QString &mode) {
    QStringList c;
    for (const Window &w : t.windows) {
        if (w.minimised || w.workspace != workspace || w.fullscreen) continue;
        if (mode == QLatin1String("desktop") && !w.floating) {
            const QRect r = w.rect;
            c << at(w.id) + QStringLiteral("floating enable");
            if (r.isValid()) {
                c << at(w.id) + QStringLiteral("resize set %1 %2").arg(r.width()).arg(r.height());
                c << at(w.id) + QStringLiteral("move absolute position %1 %2").arg(r.x()).arg(r.y());
            }
        } else if (mode == QLatin1String("tiling") && w.floating) {
            for (const QString &m : w.marks)
                if (m == QLatin1String("taos_full") || m == QLatin1String("taos_ftiled") || m.startsWith(QLatin1String("taos_fgeo_")))
                    c << at(w.id) + QStringLiteral("unmark ") + Plans::quoted(m);
            c << at(w.id) + QStringLiteral("floating disable");
        }
    }
    return c;
}

QString PlacementMemory::path() {
    const QString e = qEnvironmentVariable("TAOS_WINDOW_GEOMETRY");
    if (!e.isEmpty()) return e;
    const QString xdg = qEnvironmentVariable("XDG_CONFIG_HOME");
    return (xdg.isEmpty() ? QDir::homePath() + QStringLiteral("/.config") : xdg) + QStringLiteral("/taos/window-geometry.json");
}

bool PlacementMemory::read(const QString &file, QString *error) {
    byOutput.clear();
    QFile f(file);
    if (!f.exists()) return true;
    if (!f.open(QIODevice::ReadOnly)) { if (error) *error = QStringLiteral("cannot read %1").arg(file); return false; }
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll());
    if (!d.isObject()) { if (error) *error = QStringLiteral("%1 is not a JSON object").arg(file); return false; }
    const QJsonObject outs = d.object();
    for (auto o = outs.begin(); o != outs.end(); ++o) {
        const QJsonObject apps = o.value().toObject();
        for (auto a = apps.begin(); a != apps.end(); ++a) {
            const QJsonObject r = a.value().toObject();
            const QRect rect(r.value(QLatin1String("x")).toInt(), r.value(QLatin1String("y")).toInt(),
                             r.value(QLatin1String("w")).toInt(), r.value(QLatin1String("h")).toInt());
            if (rect.width() > 0 && rect.height() > 0) byOutput[o.key()].insert(a.key(), rect);
        }
    }
    return true;
}

bool PlacementMemory::write(const QString &file, QString *error) const {
    QJsonObject outs;
    for (auto o = byOutput.begin(); o != byOutput.end(); ++o) {
        QJsonObject apps;
        for (auto a = o.value().begin(); a != o.value().end(); ++a)
            apps.insert(a.key(), QJsonObject { { QStringLiteral("x"), a.value().x() }, { QStringLiteral("y"), a.value().y() },
                                               { QStringLiteral("w"), a.value().width() }, { QStringLiteral("h"), a.value().height() } });
        outs.insert(o.key(), apps);
    }
    QDir().mkpath(QFileInfo(file).absolutePath());
    QSaveFile f(file);
    if (!f.open(QIODevice::WriteOnly) || f.write(QJsonDocument(outs).toJson()) < 0 || !f.commit()) {
        if (error) *error = QStringLiteral("cannot write %1: %2").arg(file, f.errorString());
        return false;
    }
    return true;
}

bool PlacementMemory::remember(const Window &w) {
    if (w.appId.isEmpty() || w.output.isEmpty() || !w.floating || w.minimised || w.fullscreen || w.fullSize || !w.rect.isValid()) return false;
    byOutput[w.output].insert(w.appId, w.rect);
    return true;
}

QRect placeNewWindow(const Tree &t, const Window &w, const DesktopModes &modes, const QSize &preferred, const PlacementMemory *memory) {
    if (w.minimised || w.fullscreen) return QRect();
    // A window sway floated on its own (a dialog) is placed whatever the workspace's style; a tiled one only on a desktop workspace.
    if (!w.floating && modes.modeFor(w.workspace) != QLatin1String("desktop")) return QRect();
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) return QRect();
    const QRect a = ws->rect;   // the usable area: the output less the bar and the dock
    // Never more than 90 % of it in either direction, so a window always leaves the desktop visible around it.
    const int maxW = std::max(1, a.width() * 9 / 10), maxH = std::max(1, a.height() * 9 / 10);
    int width, height;
    if (preferred.isValid()) {
        width = std::clamp(preferred.width(), 1, maxW);
        height = std::clamp(preferred.height(), 1, maxH);
    } else {
        width = std::clamp(a.width() * 6 / 10, std::min(480, a.width()), std::min(1600, maxW));
        height = std::clamp(a.height() * 7 / 10, std::min(320, a.height()), std::min(900, maxH));
    }
    const auto inside = [&](QRect r) {
        r.moveLeft(std::clamp(r.x(), a.x(), std::max(a.x(), a.x() + a.width() - r.width())));
        r.moveTop(std::clamp(r.y(), a.y(), std::max(a.y(), a.y() + a.height() - r.height())));
        return r;
    };
    // Where this app's window was when it was last closed on this output, while it still fits.
    if (memory && !w.appId.isEmpty()) {
        const QRect m = memory->recall(ws->output, w.appId);
        if (m.isValid() && a.contains(m) && m.width() <= maxW && m.height() <= maxH) {
            QRect r = m;
            for (int i = 0; i < 6; ++i) {   // an open window of the app already sits there: step down and right
                bool taken = false;
                for (const Window &o : t.windows)
                    if (o.id != w.id && !o.minimised && o.floating && o.workspace == w.workspace && o.appId == w.appId && o.rect.topLeft() == r.topLeft()) taken = true;
                if (!taken) return r;
                const QRect next = r.translated(32, 32);
                if (!a.contains(next)) break;
                r = next;
            }
        }
    }
    // A dialog: a smaller window of a process that already has a window open here is centred on that one.
    if (preferred.isValid() && w.pid > 0) {
        const Window *parent = nullptr;
        for (const Window &o : t.windows) {
            if (o.id == w.id || o.minimised || o.pid != w.pid || o.workspace != w.workspace || !o.rect.isValid()) continue;
            if (qint64(width) * height * 10 >= qint64(o.rect.width()) * o.rect.height() * 6) continue;
            if (!parent || (o.focused && !parent->focused) || (o.focused == parent->focused && o.id > parent->id)) parent = &o;
        }
        if (parent) {
            QRect r(0, 0, width, height);
            r.moveCenter(parent->rect.center());
            return inside(r);
        }
    }
    // Centred, or 32 px down and right of the last window of the same app, or stepped like other windows.
    int others = 0;
    const Window *sibling = nullptr;
    for (const Window &o : t.windows) {
        if (o.id == w.id || o.minimised || !o.floating || o.workspace != w.workspace) continue;
        ++others;
        if (!w.appId.isEmpty() && o.appId == w.appId && o.rect.isValid() && (!sibling || o.id > sibling->id)) sibling = &o;
    }
    QRect r(0, 0, width, height);
    if (sibling) {
        r.moveTopLeft(sibling->rect.topLeft() + QPoint(32, 32));
        if (!a.contains(r)) r.moveTopLeft(QPoint(a.x() + (a.width() - width) / 2, a.y() + (a.height() - height) / 2));
    } else {
        const int step = 32 * (others % 6);
        r.moveTopLeft(QPoint(a.x() + (a.width() - width) / 2 + step - 80, a.y() + (a.height() - height) / 2 + step - 80));
        if (others == 0) r.moveTopLeft(QPoint(a.x() + (a.width() - width) / 2, a.y() + (a.height() - height) / 2));
    }
    return inside(r);
}

QRect newWindowGeometry(const Tree &t, const Window &w, const DesktopModes &modes) {
    if (w.floating) return QRect();
    return placeNewWindow(t, w, modes, QSize());
}

QMap<int, QRect> modeSwitchTargets(const Tree &t, const QString &workspace) {
    QMap<int, QRect> m;
    for (const Window &w : t.windows)
        if (!w.minimised && !w.fullscreen && !w.floating && w.workspace == workspace && w.rect.isValid()) m.insert(w.id, w.rect);
    return m;
}

QStringList newWindowCommands(const Tree &t, const Window &w, const DesktopModes &modes) {
    const QRect g = newWindowGeometry(t, w, modes);
    if (!g.isValid()) return {};
    return {at(w.id) + QStringLiteral("floating enable"),
            at(w.id) + QStringLiteral("resize set %1 %2").arg(g.width()).arg(g.height()),
            at(w.id) + QStringLiteral("move absolute position %1 %2").arg(g.x()).arg(g.y())};
}

}  // namespace TaosWin
