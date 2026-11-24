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

QRect placeNewWindow(const Tree &t, const Window &w, const DesktopModes &modes, const QSize &preferred) {
    if (w.minimised || w.fullscreen) return QRect();
    if (modes.modeFor(w.workspace) != QLatin1String("desktop")) return QRect();
    const Workspace *ws = t.workspace(w.workspace);
    if (!ws || !ws->rect.isValid()) return QRect();
    int others = 0;
    for (const Window &o : t.windows)
        if (o.id != w.id && !o.minimised && o.floating && o.workspace == w.workspace) ++others;
    const QRect a = ws->rect;
    int width, height;
    if (preferred.isValid()) {
        // The application asked for this size: keep it, but never larger than the free area (bar and dock excluded).
        width = std::clamp(preferred.width(), std::min(480, a.width()), std::max(a.width() - 32, 1));
        height = std::clamp(preferred.height(), std::min(320, a.height()), std::max(a.height() - 32, 1));
    } else {
        width = std::clamp(a.width() * 6 / 10, std::min(480, a.width()), std::max(a.width() - 40, 1));
        height = std::clamp(a.height() * 7 / 10, std::min(320, a.height()), std::max(a.height() - 40, 1));
    }
    const int step = 32 * (others % 6);
    int x = a.x() + (a.width() - width) / 2 + step - 80;
    int y = a.y() + (a.height() - height) / 2 + step - 80;
    x = std::clamp(x, a.x(), std::max(a.x(), a.right() - width));
    y = std::clamp(y, a.y(), std::max(a.y(), a.bottom() - height));
    return QRect(x, y, width, height);
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
