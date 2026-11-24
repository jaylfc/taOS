// SPDX-License-Identifier: AGPL-3.0-or-later
#include "desktopmode.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>

static QString cleanMode(const QString &m) { return m == QLatin1String("tiling") ? m : QStringLiteral("desktop"); }

QString DesktopModes::modeFor(const QString &ws) const {
    const auto it = workspaces.constFind(ws);
    return it == workspaces.constEnd() ? defaultMode : *it;
}

namespace DesktopModeFile {
QString path() {
    const QString o = qEnvironmentVariable("TAOS_DESKTOP_MODE");
    if (!o.isEmpty()) return o;
    QString base = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.config");
    return base + QStringLiteral("/taos/desktop.json");
}

bool read(const QString &p, DesktopModes *out) {
    *out = DesktopModes();
    QFile f(p);
    if (!f.open(QIODevice::ReadOnly)) return false;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll());
    if (!d.isObject()) return false;
    const QJsonObject o = d.object();
    out->defaultMode = cleanMode(o.value(QStringLiteral("default")).toString());
    const QJsonObject ws = o.value(QStringLiteral("workspaces")).toObject();
    for (auto it = ws.constBegin(); it != ws.constEnd(); ++it) out->workspaces.insert(it.key(), cleanMode(it.value().toString()));
    return true;
}
}  // namespace DesktopModeFile

DesktopModeWatcher::DesktopModeWatcher(const QString &path, bool watch, QObject *parent)
    : QObject(parent), m_path(path.isEmpty() ? DesktopModeFile::path() : path), m_watching(watch) {
    DesktopModeFile::read(m_path, &m_m);
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(50);
    connect(&m_debounce, &QTimer::timeout, this, &DesktopModeWatcher::reload);
    if (watch) {
        connect(&m_watch, &QFileSystemWatcher::fileChanged, this, [this] { m_debounce.start(); });
        connect(&m_watch, &QFileSystemWatcher::directoryChanged, this, [this] { m_debounce.start(); });
        rewatch();
    }
}

void DesktopModeWatcher::rewatch() {
    if (!m_watching) return;
    const QString dir = QFileInfo(m_path).absolutePath();
    QDir().mkpath(dir);
    if (!m_watch.directories().contains(dir)) m_watch.addPath(dir);
    if (QFileInfo::exists(m_path) && !m_watch.files().contains(m_path)) m_watch.addPath(m_path);
}

void DesktopModeWatcher::reload() {
    DesktopModes m;
    DesktopModeFile::read(m_path, &m);   // missing or broken: everything is desktop mode
    rewatch();
    if (m == m_m) return;
    m_m = m;
    emit changed();
}
