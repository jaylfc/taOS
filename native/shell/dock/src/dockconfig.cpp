#include "dockconfig.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QStandardPaths>

namespace DockConfigFile {

QString userPath() {
    const QString o = qEnvironmentVariable("TAOS_DOCK_CONFIG");
    if (!o.isEmpty()) return o;
    QString base = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (base.isEmpty()) base = QDir::homePath() + QStringLiteral("/.config");
    return base + QStringLiteral("/taos/dock.json");
}

bool validPosition(const QString &v) { return v == QLatin1String("bottom") || v == QLatin1String("left") || v == QLatin1String("right"); }
bool validSize(const QString &v) { return v == QLatin1String("small") || v == QLatin1String("medium") || v == QLatin1String("large"); }
bool validOutputs(const QString &v) { return v == QLatin1String("primary") || v == QLatin1String("all"); }

bool parseBool(const QString &v, bool *out) {
    const QString s = v.trimmed().toLower();
    if (s == QLatin1String("true") || s == QLatin1String("on") || s == QLatin1String("yes") || s == QLatin1String("1")) { *out = true; return true; }
    if (s == QLatin1String("false") || s == QLatin1String("off") || s == QLatin1String("no") || s == QLatin1String("0")) { *out = false; return true; }
    return false;
}

bool read(const QString &path, DockConfig *out, QString *error) {
    *out = DockConfig();
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) { if (error) *error = f.errorString(); return false; }
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    if (pe.error != QJsonParseError::NoError || !d.isObject()) {
        if (error) *error = pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not a JSON object");
        return false;
    }
    const QJsonObject o = d.object();
    const QString pos = o.value(QStringLiteral("position")).toString();
    if (validPosition(pos)) out->position = pos;
    out->attached = o.value(QStringLiteral("attached")).toBool(false);
    out->autohide = o.value(QStringLiteral("autohide")).toBool(false);
    const QString size = o.value(QStringLiteral("size")).toString();
    if (validSize(size)) out->size = size;
    out->magnify = o.value(QStringLiteral("magnify")).toBool(false);
    out->hideInTiling = o.value(QStringLiteral("hideInTiling")).toBool(true);
    const QString outs = o.value(QStringLiteral("outputs")).toString();
    if (validOutputs(outs)) out->outputs = outs;
    if (o.value(QStringLiteral("pinned")).isArray()) {
        out->pinnedSet = true;
        for (const QJsonValue &v : o.value(QStringLiteral("pinned")).toArray()) {
            const QString id = v.toString();
            if (!id.isEmpty() && !out->pinned.contains(id)) out->pinned << id;
        }
    }
    return true;
}

bool write(const QString &path, const DockConfig &c, QString *error) {
    QDir().mkpath(QFileInfo(path).absolutePath());
    QJsonObject o;
    o[QStringLiteral("position")] = c.position;
    o[QStringLiteral("attached")] = c.attached;
    o[QStringLiteral("autohide")] = c.autohide;
    o[QStringLiteral("size")] = c.size;
    o[QStringLiteral("magnify")] = c.magnify;
    o[QStringLiteral("hideInTiling")] = c.hideInTiling;
    if (c.pinnedSet) o[QStringLiteral("pinned")] = QJsonArray::fromStringList(c.pinned);
    o[QStringLiteral("outputs")] = c.outputs;
    QSaveFile f(path);   // temp file next to the target, renamed over it on commit
    if (!f.open(QIODevice::WriteOnly)) { if (error) *error = f.errorString(); return false; }
    f.write(QJsonDocument(o).toJson(QJsonDocument::Indented));
    if (!f.commit()) { if (error) *error = f.errorString(); return false; }
    return true;
}

bool setKey(DockConfig *c, const QString &key, const QString &value, QString *error) {
    auto fail = [&](const QString &m) { if (error) *error = m; return false; };
    bool b = false;
    if (key == QLatin1String("position")) {
        if (!validPosition(value)) return fail(QStringLiteral("position must be bottom, left or right"));
        c->position = value;
    } else if (key == QLatin1String("size")) {
        if (!validSize(value)) return fail(QStringLiteral("size must be small, medium or large"));
        c->size = value;
    } else if (key == QLatin1String("outputs")) {
        if (!validOutputs(value)) return fail(QStringLiteral("outputs must be primary or all"));
        c->outputs = value;
    } else if (key == QLatin1String("attached") || key == QLatin1String("autohide") || key == QLatin1String("magnify") || key == QLatin1String("hideInTiling")) {
        if (!parseBool(value, &b)) return fail(key + QStringLiteral(" must be true or false"));
        if (key == QLatin1String("attached")) c->attached = b;
        else if (key == QLatin1String("autohide")) c->autohide = b;
        else if (key == QLatin1String("hideInTiling")) c->hideInTiling = b;
        else c->magnify = b;
    } else {
        return fail(QStringLiteral("unknown key '%1' (position, attached, autohide, size, magnify, hideInTiling, outputs; use pin/unpin for the pinned list)").arg(key));
    }
    return true;
}

QString getKey(const DockConfig &c, const QString &key, bool *ok) {
    if (ok) *ok = true;
    if (key == QLatin1String("position")) return c.position;
    if (key == QLatin1String("size")) return c.size;
    if (key == QLatin1String("outputs")) return c.outputs;
    if (key == QLatin1String("attached")) return c.attached ? QStringLiteral("true") : QStringLiteral("false");
    if (key == QLatin1String("autohide")) return c.autohide ? QStringLiteral("true") : QStringLiteral("false");
    if (key == QLatin1String("hideInTiling")) return c.hideInTiling ? QStringLiteral("true") : QStringLiteral("false");
    if (key == QLatin1String("magnify")) return c.magnify ? QStringLiteral("true") : QStringLiteral("false");
    if (key == QLatin1String("pinned")) return c.pinned.join(QLatin1Char(' '));
    if (ok) *ok = false;
    return {};
}

}  // namespace DockConfigFile

DockSettings::DockSettings(const QString &path, bool watch, QObject *parent)
    : QObject(parent), m_path(path.isEmpty() ? DockConfigFile::userPath() : path), m_watching(watch) {
    DockConfigFile::read(m_path, &m_c);
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(50);
    connect(&m_debounce, &QTimer::timeout, this, &DockSettings::reload);
    if (watch) {
        connect(&m_watch, &QFileSystemWatcher::fileChanged, this, [this] { m_debounce.start(); });
        connect(&m_watch, &QFileSystemWatcher::directoryChanged, this, [this] { m_debounce.start(); });
        rewatch();
    }
}

void DockSettings::rewatch() {
    if (!m_watching) return;
    const QString dir = QFileInfo(m_path).absolutePath();
    QDir().mkpath(dir);
    if (!m_watch.directories().contains(dir)) m_watch.addPath(dir);
    if (QFileInfo::exists(m_path) && !m_watch.files().contains(m_path)) m_watch.addPath(m_path);
}

void DockSettings::reload() {
    DockConfig c;
    DockConfigFile::read(m_path, &c);   // a missing or broken file means the defaults
    rewatch();
    if (c == m_c) return;
    const bool pinChanged = c.pinned != m_c.pinned || c.pinnedSet != m_c.pinnedSet;
    const bool other = [&] { DockConfig a = c, b = m_c; a.pinned = b.pinned = {}; a.pinnedSet = b.pinnedSet; return a != b; }();
    m_c = c;
    if (other) emit changed();
    if (pinChanged) emit pinnedChanged();
}

bool DockSettings::commit(const DockConfig &c) {
    m_err.clear();
    if (!DockConfigFile::write(m_path, c, &m_err)) return false;
    rewatch();
    const DockConfig old = m_c;
    m_c = c;
    const bool pinChanged = c.pinned != old.pinned || c.pinnedSet != old.pinnedSet;
    DockConfig a = c, b = old;
    a.pinned = b.pinned = {};
    a.pinnedSet = b.pinnedSet = false;
    if (a != b) emit changed();
    if (pinChanged) emit pinnedChanged();
    return true;
}

bool DockSettings::set(const QString &key, const QString &value) {
    DockConfig c = m_c;
    m_err.clear();
    if (!DockConfigFile::setKey(&c, key, value, &m_err)) return false;
    return commit(c);
}

bool DockSettings::setPinned(const QStringList &ids) {
    DockConfig c = m_c;
    c.pinned = ids;
    c.pinnedSet = true;
    return commit(c);
}
