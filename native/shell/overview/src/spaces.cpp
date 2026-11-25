// SPDX-License-Identifier: AGPL-3.0-or-later
#include "spaces.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSaveFile>
#include <QStandardPaths>
#include <algorithm>

#include "core/windowmodel.h"

namespace Spaces {

QString defaultLabel(int position) { return QStringLiteral("Desktop %1").arg(position); }

QString cleanName(const QString &raw) {
    QString s;
    for (const QChar c : raw) s += c.isSpace() || c.unicode() < 32 ? QLatin1Char(' ') : c;
    s = s.simplified();
    return s.left(40).trimmed();
}

QString Store::defaultPath() {
    const QString o = qEnvironmentVariable("TAOS_SPACES_FILE");
    if (!o.isEmpty()) return o;
    return QStandardPaths::writableLocation(QStandardPaths::GenericConfigLocation) + QStringLiteral("/taos/overview-spaces.json");
}

bool Store::load(QString *error) {
    m_lists.clear();
    m_current.clear();
    QFile f(m_path);
    if (!f.exists()) return true;
    const auto damaged = [&](const QString &why) {
        const QString aside = m_path + QStringLiteral(".damaged-") + QString::number(QDateTime::currentSecsSinceEpoch());
        QFile::rename(m_path, aside);
        m_lists.clear();
        m_current.clear();
        if (error) *error = QStringLiteral("the spaces file was unreadable (%1); kept as %2 and started a new one").arg(why, aside);
        return false;
    };
    if (!f.open(QIODevice::ReadOnly)) {
        if (error) *error = QStringLiteral("cannot read %1: %2").arg(m_path, f.errorString());
        return false;   // not moved aside: it may only be a permission slip
    }
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
    f.close();
    if (!d.isObject()) return damaged(pe.error != QJsonParseError::NoError ? pe.errorString() : QStringLiteral("not an object"));
    const QJsonObject outs = d.object().value(QLatin1String("outputs")).toObject();
    for (auto it = outs.begin(); it != outs.end(); ++it) {
        const QJsonObject o = it.value().toObject();
        QVector<Space> list;
        for (const QJsonValue &v : o.value(QLatin1String("spaces")).toArray()) {
            const QJsonObject so = v.toObject();
            const QString key = so.value(QLatin1String("key")).toString();
            if (key.isEmpty() || outputOf(key).size()) continue;
            bool dup = false;
            for (const Space &s : list) dup = dup || s.key == key;
            if (dup) continue;
            list.push_back({ key, cleanName(so.value(QLatin1String("name")).toString()) });
        }
        m_lists.insert(it.key(), list);
        const QString cur = o.value(QLatin1String("current")).toString();
        if (!cur.isEmpty()) m_current.insert(it.key(), cur);
    }
    return true;
}

bool Store::save(QString *error) const {
    QJsonObject outs;
    for (auto it = m_lists.begin(); it != m_lists.end(); ++it) {
        QJsonArray arr;
        for (const Space &s : it.value()) arr.append(QJsonObject { { QStringLiteral("key"), s.key }, { QStringLiteral("name"), s.name } });
        QJsonObject o { { QStringLiteral("spaces"), arr } };
        if (m_current.contains(it.key())) o.insert(QStringLiteral("current"), m_current.value(it.key()));
        outs.insert(it.key(), o);
    }
    const QJsonObject root { { QStringLiteral("version"), 1 }, { QStringLiteral("outputs"), outs } };
    if (!QDir().mkpath(QFileInfo(m_path).absolutePath())) {
        if (error) *error = QStringLiteral("cannot create the folder for %1").arg(m_path);
        return false;
    }
    QSaveFile f(m_path);
    if (!f.open(QIODevice::WriteOnly) || f.write(QJsonDocument(root).toJson(QJsonDocument::Indented)) < 0 || !f.commit()) {
        if (error) *error = QStringLiteral("cannot write %1: %2").arg(m_path, f.errorString());
        return false;
    }
    return true;
}

bool Store::has(const QString &key) const { return !outputOf(key).isEmpty(); }

QString Store::outputOf(const QString &key) const {
    for (auto it = m_lists.begin(); it != m_lists.end(); ++it)
        for (const Space &s : it.value()) if (s.key == key) return it.key();
    return {};
}

int Store::positionOf(const QString &output, const QString &key) const {
    const QVector<Space> l = m_lists.value(output);
    for (int i = 0; i < l.size(); ++i) if (l[i].key == key) return i;
    return -1;
}

QString Store::label(const QString &output, const QString &key) const {
    const int p = positionOf(output, key);
    if (p < 0) return key;
    const QString n = m_lists.value(output)[p].name;
    return n.isEmpty() ? defaultLabel(p + 1) : n;
}

QString Store::add(const QString &output, const QString &key, const QString &name) {
    if (key.isEmpty()) return QStringLiteral("empty space key");
    if (has(key)) return QStringLiteral("space %1 already exists").arg(key);
    m_lists[output].push_back({ key, cleanName(name) });
    return {};
}

QString Store::rename(const QString &key, const QString &name) {
    const QString o = outputOf(key);
    if (o.isEmpty()) return QStringLiteral("no such space");
    for (Space &s : m_lists[o]) if (s.key == key) s.name = cleanName(name);
    return {};
}

QString Store::move(const QString &key, int to) {
    const QString o = outputOf(key);
    if (o.isEmpty()) return QStringLiteral("no such space");
    QVector<Space> &l = m_lists[o];
    const int from = positionOf(o, key);
    to = std::max(0, std::min(int(l.size()) - 1, to));
    const Space s = l.takeAt(from);
    l.insert(to, s);
    return {};
}

void Store::relocate(const QString &key, const QString &output) {
    const QString o = outputOf(key);
    if (o.isEmpty() || o == output) return;
    QVector<Space> &l = m_lists[o];
    const int p = positionOf(o, key);
    const Space s = l.takeAt(p);
    m_lists[output].push_back(s);
    if (m_current.value(o) == key) m_current.remove(o);
}

QString Store::remove(const QString &key) {
    const QString o = outputOf(key);
    if (o.isEmpty()) return QStringLiteral("no such space");
    QVector<Space> &l = m_lists[o];
    if (l.size() <= 1) return QStringLiteral("this is the only space, so it cannot be removed");
    l.remove(positionOf(o, key));
    if (m_current.value(o) == key) m_current.remove(o);
    return {};
}

QString Store::freshKey(const QStringList &alsoUsed) const {
    for (int n = 1;; ++n) {
        const QString k = QString::number(n);
        if (!has(k) && !alsoUsed.contains(k)) return k;
    }
}

// ---- Engine -------------------------------------------------------------------------------------

QString Engine::wsRef(const QString &key) {
    bool ok = false;
    const int n = key.toInt(&ok);
    if (ok && n >= 0 && QString::number(n) == key) return QStringLiteral("number %1").arg(n);
    QString q = key;
    q.replace(QLatin1Char('\\'), QStringLiteral("\\\\")).replace(QLatin1Char('"'), QStringLiteral("\\\""));
    return QLatin1Char('"') + q + QLatin1Char('"');
}

QString Engine::save() {
    QString err;
    if (m_store->save(&err)) { m_lastError.clear(); return {}; }
    m_lastError = err;
    fprintf(stderr, "taos-overview: %s\n", qPrintable(err));
    return err;
}

void Engine::sync(const Snapshot &s, const QString &output) {
    m_snap = s;
    m_output = output;
    bool changed = false;
    QHash<QString, int> held;   // windows by "output\nworkspace"
    for (const WinInfo &w : s.windows) held[w.output + QLatin1Char('\n') + w.workspace]++;
    QVector<WsInfo> live = s.workspaces;
    std::stable_sort(live.begin(), live.end(), [](const WsInfo &a, const WsInfo &b) {
        const int na = a.num < 0 ? 1 << 20 : a.num, nb = b.num < 0 ? 1 << 20 : b.num;
        return na < nb;
    });
    for (const WsInfo &ws : live) {
        if (ws.name.startsWith(QLatin1String("__"))) continue;
        const QString known = m_store->outputOf(ws.name);
        if (!known.isEmpty()) {
            if (known != ws.output) { m_store->relocate(ws.name, ws.output); changed = true; }   // it follows its workspace
            continue;
        }
        const bool has = held.value(ws.output + QLatin1Char('\n') + ws.name) > 0;
        // A bare workspace is only a ghost (sway came up on it, or the person is passing through) when the
        // person already has spaces on this output; on a first run it becomes their first space.
        if (!has && !m_store->spaces(ws.output).isEmpty()) continue;
        bool numeric = false;
        ws.name.toInt(&numeric);
        m_store->add(ws.output, ws.name, numeric ? QString() : ws.name);
        changed = true;
    }
    QStringList liveNames;
    for (const WsInfo &w : s.workspaces) liveNames << w.name;
    for (const OutInfo &o : s.outputs) {
        if (m_store->spaces(o.name).isEmpty()) {
            m_store->add(o.name, m_store->freshKey(liveNames));
            changed = true;
        }
        if (!o.currentWorkspace.isEmpty() && m_store->outputOf(o.currentWorkspace) == o.name
            && m_store->current(o.name) != o.currentWorkspace) {
            m_store->setCurrent(o.name, o.currentWorkspace);
            changed = true;
        }
    }
    if (!m_started) {
        m_started = true;
        // sway came up on a bare workspace that is not one of the person's spaces: take them back to theirs.
        for (const OutInfo &o : s.outputs) {
            if (o.name != output) continue;
            const QString saved = m_store->current(o.name);
            if (!saved.isEmpty() && o.currentWorkspace != saved && !m_store->has(o.currentWorkspace)
                && m_store->outputOf(saved) == o.name)
                m_sink->command(QStringLiteral("workspace ") + wsRef(saved));
        }
    }
    if (changed) save();
}

QVector<View> Engine::views() const {
    QVector<View> out;
    QString cur;
    for (const OutInfo &o : m_snap.outputs) if (o.name == m_output) cur = o.currentWorkspace;
    const QVector<Space> list = m_store->spaces(m_output);
    for (int i = 0; i < list.size(); ++i) {
        View v;
        v.key = list[i].key;
        v.position = i + 1;
        v.custom = !list[i].name.isEmpty();
        v.label = v.custom ? list[i].name : defaultLabel(i + 1);
        v.current = list[i].key == cur;
        for (const WsInfo &w : m_snap.workspaces) if (w.name == v.key) { v.live = true; v.urgent = w.urgent; }
        for (const WinInfo &w : m_snap.windows) if (w.workspace == v.key && w.output == m_output && !w.minimised) v.windows++;
        out.push_back(v);
    }
    return out;
}

QString Engine::add(const QString &name, QString *newKey) {
    if (m_output.isEmpty()) return QStringLiteral("no output");
    QStringList used;
    for (const WsInfo &w : m_snap.workspaces) used << w.name;
    const QString key = m_store->freshKey(used);
    const QString e = m_store->add(m_output, key, name);
    if (!e.isEmpty()) return e;
    if (newKey) *newKey = key;
    return save();
}

QString Engine::rename(const QString &key, const QString &name) {
    const QString e = m_store->rename(key, name);
    return e.isEmpty() ? save() : e;
}

QString Engine::move(const QString &key, int toPosition) {
    const QString e = m_store->move(key, toPosition);
    return e.isEmpty() ? save() : e;
}

QString Engine::go(const QString &key) {
    if (!m_store->has(key)) return QStringLiteral("no such space");
    m_sink->command(QStringLiteral("workspace ") + wsRef(key));
    return {};
}

QString Engine::moveWindow(int conId, const QString &key) {
    if (!m_store->has(key)) return QStringLiteral("no such space");
    for (const WinInfo &w : m_snap.windows) {
        if (w.conId != conId) continue;
        if (w.minimised) {   // it comes back to the workspace its mark names: point the mark at the new one
            m_sink->command(QStringLiteral("[con_id=%1] unmark \"taos_ws_%2\"").arg(conId).arg(TaosWin::Marks::encodeName(w.workspace)));
            m_sink->command(QStringLiteral("[con_id=%1] mark --add \"taos_ws_%2\"").arg(conId).arg(TaosWin::Marks::encodeName(key)));
        } else if (w.workspace != key) {
            m_sink->command(QStringLiteral("[con_id=%1] move container to workspace %2").arg(conId).arg(wsRef(key)));
        }
        return {};
    }
    return QStringLiteral("no such window");
}

QString Engine::remove(const QString &key) {
    const QString out = m_store->outputOf(key);
    if (out.isEmpty()) return QStringLiteral("no such space");
    const QVector<Space> before = m_store->spaces(out);
    const int pos = m_store->positionOf(out, key);
    if (before.size() <= 1) return QStringLiteral("this is the only space, so it cannot be removed");
    const QString neighbour = before[pos + 1 < before.size() ? pos + 1 : pos - 1].key;
    QString cur;
    for (const OutInfo &o : m_snap.outputs) if (o.name == out) cur = o.currentWorkspace;
    // Write the new list first: when it cannot be saved nothing moves and the person is told.
    const Store backup = *m_store;
    m_store->remove(key);
    if (!save().isEmpty()) {
        *m_store = backup;
        return m_lastError;
    }
    if (cur == key) m_sink->command(QStringLiteral("workspace ") + wsRef(neighbour));
    for (const WinInfo &w : m_snap.windows) {
        if (w.workspace != key || w.output != out) continue;
        if (w.minimised) {
            m_sink->command(QStringLiteral("[con_id=%1] unmark \"taos_ws_%2\"").arg(w.conId).arg(TaosWin::Marks::encodeName(key)));
            m_sink->command(QStringLiteral("[con_id=%1] mark --add \"taos_ws_%2\"").arg(w.conId).arg(TaosWin::Marks::encodeName(neighbour)));
        } else {
            m_sink->command(QStringLiteral("[con_id=%1] move container to workspace %2").arg(w.conId).arg(wsRef(neighbour)));
        }
    }
    return {};
}

QString Engine::keyFor(const QString &s) const {
    if (s.startsWith(QLatin1String("key:"))) return s.mid(4);
    bool ok = false;
    const int n = s.toInt(&ok);
    const QVector<Space> l = m_store->spaces(m_output);
    if (!ok || n < 1 || n > l.size()) return {};
    return l[n - 1].key;
}

QString Engine::handle(const QStringList &w) {
    // w[0] is "space"
    const QString verb = w.value(1);
    const auto reply = [&](const QString &err, const QString &ok = QStringLiteral("ok")) {
        return err.isEmpty() ? ok : QStringLiteral("error: ") + err;
    };
    if (verb == QLatin1String("list") || verb.isEmpty()) {
        QJsonArray a;
        for (const View &v : views())
            a.append(QJsonObject { { QStringLiteral("position"), v.position }, { QStringLiteral("key"), v.key },
                                   { QStringLiteral("name"), v.label }, { QStringLiteral("custom"), v.custom },
                                   { QStringLiteral("current"), v.current }, { QStringLiteral("windows"), v.windows },
                                   { QStringLiteral("output"), m_output } });
        return QStringLiteral("ok ") + QString::fromUtf8(QJsonDocument(a).toJson(QJsonDocument::Compact));
    }
    if (verb == QLatin1String("add")) {
        QString key;
        const QString e = add(w.mid(2).join(QLatin1Char(' ')), &key);
        return reply(e, QStringLiteral("ok {\"key\":\"%1\",\"position\":%2}").arg(key).arg(m_store->positionOf(m_output, key) + 1));
    }
    const QString key = keyFor(w.value(2));
    if (verb == QLatin1String("rename") || verb == QLatin1String("move") || verb == QLatin1String("remove")
        || verb == QLatin1String("go")) {
        if (key.isEmpty()) return QStringLiteral("error: no such space '%1' (use its position, 1 to %2)").arg(w.value(2)).arg(m_store->spaces(m_output).size());
        if (verb == QLatin1String("rename")) return reply(rename(key, w.mid(3).join(QLatin1Char(' '))));
        if (verb == QLatin1String("go")) return reply(go(key));
        if (verb == QLatin1String("remove")) return reply(remove(key));
        bool ok = false;
        const int to = w.value(3).toInt(&ok);
        if (!ok || to < 1) return QStringLiteral("error: usage: space move <position> <new position>");
        return reply(move(key, to - 1));
    }
    if (verb == QLatin1String("move-window")) {
        bool ok = false;
        const int id = w.value(2).toInt(&ok);
        const QString k = keyFor(w.value(3));
        if (!ok || k.isEmpty()) return QStringLiteral("error: usage: space move-window <window id> <position>");
        return reply(moveWindow(id, k));
    }
    return QStringLiteral("error: unknown space command '%1' (list, add, rename, move, remove, go, move-window)").arg(verb);
}

}  // namespace Spaces
