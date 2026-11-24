// SPDX-License-Identifier: AGPL-3.0-or-later
#include "filesettings.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QSaveFile>
#include <QStandardPaths>
#include <QVariantMap>

#include "core/desktopmodes.h"

using namespace Qt::StringLiterals;

namespace TaosSettings {

JsonFile::JsonFile(const QString &path, bool watch, QObject *parent) : QObject(parent), m_path(path)
{
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(60);
    connect(&m_debounce, &QTimer::timeout, this, [this] { reload(); rewatch(); });
    if (watch) {
        connect(&m_watch, &QFileSystemWatcher::fileChanged, &m_debounce, qOverload<>(&QTimer::start));
        connect(&m_watch, &QFileSystemWatcher::directoryChanged, &m_debounce, qOverload<>(&QTimer::start));
        rewatch();
    }
    reload();
}

void JsonFile::rewatch()
{
    const QString dir = QFileInfo(m_path).absolutePath();
    QDir().mkpath(dir);
    if (!m_watch.directories().contains(dir))
        m_watch.addPath(dir);
    if (QFileInfo::exists(m_path) && !m_watch.files().contains(m_path))
        m_watch.addPath(m_path);
}

void JsonFile::reload()
{
    QJsonObject obj;
    bool exists = false;
    QString err;
    QFile f(m_path);
    if (f.open(QIODevice::ReadOnly)) {
        exists = true;
        QJsonParseError pe;
        const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
        if (pe.error == QJsonParseError::NoError && d.isObject())
            obj = d.object();
        else
            err = tr("The settings file could not be read, so defaults are shown.");
    }
    if (obj != m_obj || exists != m_exists || err != m_error) {
        m_obj = obj;
        m_exists = exists;
        m_error = err;
        emit changed();
    }
}

bool JsonFile::merge(const QJsonObject &patch)
{
    QJsonObject next = m_obj;
    for (auto it = patch.begin(); it != patch.end(); ++it) {
        if (it.value().isNull())
            next.remove(it.key());
        else
            next.insert(it.key(), it.value());
    }
    QDir().mkpath(QFileInfo(m_path).absolutePath());
    QSaveFile f(m_path);   // temp file in the same folder, then rename
    if (!f.open(QIODevice::WriteOnly)) {
        m_error = tr("The setting could not be saved: %1").arg(f.errorString());
        emit changed();
        return false;
    }
    f.write(QJsonDocument(next).toJson(QJsonDocument::Indented));
    if (!f.commit()) {
        m_error = tr("The setting could not be saved: %1").arg(f.errorString());
        emit changed();
        return false;
    }
    m_error.clear();
    m_obj = next;
    m_exists = true;
    rewatch();
    emit changed();
    return true;
}

// ---- dock -----------------------------------------------------------------------------------

QString DockSettings::defaultPath()
{
    if (qEnvironmentVariableIsSet("TAOS_DOCK_CONFIG"))
        return qEnvironmentVariable("TAOS_DOCK_CONFIG");
    return QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation) + u"/taos/dock.json"_s;
}

DockSettings::DockSettings(const QString &path, bool watch, QObject *parent) : QObject(parent), m_file(path, watch)
{
    connect(&m_file, &JsonFile::changed, this, &DockSettings::changed);
}

static bool boolKey(const QJsonObject &o, const QString &k, bool def)
{
    const QJsonValue v = o.value(k);
    return v.isBool() ? v.toBool() : def;
}

QString DockSettings::position() const
{
    const QString v = m_file.object().value(u"position"_s).toString();
    return v == u"left" || v == u"right" || v == u"bottom" ? v : u"bottom"_s;
}
QString DockSettings::size() const
{
    const QString v = m_file.object().value(u"size"_s).toString();
    return v == u"small" || v == u"medium" || v == u"large" ? v : u"medium"_s;
}
bool DockSettings::attached() const { return boolKey(m_file.object(), u"attached"_s, false); }
bool DockSettings::autohide() const { return boolKey(m_file.object(), u"autohide"_s, false); }
bool DockSettings::magnify() const { return boolKey(m_file.object(), u"magnify"_s, false); }
bool DockSettings::hideInTiling() const { return boolKey(m_file.object(), u"hideInTiling"_s, true); }
bool DockSettings::dockInstalled() const { return !QStandardPaths::findExecutable(u"taos-dock"_s).isEmpty(); }

bool DockSettings::set(const QString &key, const QVariant &value)
{
    if (key == u"position") {
        const QString v = value.toString();
        if (v != u"bottom" && v != u"left" && v != u"right")
            return false;
        return m_file.merge({{key, v}});
    }
    if (key == u"size") {
        const QString v = value.toString();
        if (v != u"small" && v != u"medium" && v != u"large")
            return false;
        return m_file.merge({{key, v}});
    }
    if (key == u"attached" || key == u"autohide" || key == u"magnify" || key == u"hideInTiling")
        return m_file.merge({{key, value.toBool()}});
    return false;
}

// ---- desktop modes ------------------------------------------------------------------------

QString DesktopSettings::defaultPath() { return TaosWin::Modes::path(); }

DesktopSettings::DesktopSettings(const QString &path, bool watch, QObject *parent) : QObject(parent), m_file(path, watch)
{
    connect(&m_file, &JsonFile::changed, this, &DesktopSettings::changed);
}

QString DesktopSettings::defaultMode() const
{
    const QString v = m_file.object().value(u"default"_s).toString();
    return TaosWin::Modes::valid(v) ? v : u"desktop"_s;
}

QVariantList DesktopSettings::workspaces() const
{
    QVariantList out;
    const QJsonObject ws = m_file.object().value(u"workspaces"_s).toObject();
    QStringList names = ws.keys();
    names.sort();
    for (const QString &n : names) {
        const QString m = ws.value(n).toString();
        if (TaosWin::Modes::valid(m))
            out << QVariantMap{{u"name"_s, n}, {u"mode"_s, m}};
    }
    return out;
}

bool DesktopSettings::setDefaultMode(const QString &mode)
{
    if (!TaosWin::Modes::valid(mode))
        return false;
    return m_file.merge({{u"default"_s, mode}});
}

bool DesktopSettings::useDefaultFor(const QString &workspace)
{
    QJsonObject ws = m_file.object().value(u"workspaces"_s).toObject();
    if (!ws.contains(workspace))
        return true;
    ws.remove(workspace);
    return m_file.merge({{u"workspaces"_s, ws}});
}

} // namespace TaosSettings
