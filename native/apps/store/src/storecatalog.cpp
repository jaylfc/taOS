// SPDX-License-Identifier: AGPL-3.0-or-later
#include "storecatalog.h"

#include <QRegularExpression>
#include <QUrl>
#include <algorithm>

using namespace Qt::StringLiterals;

namespace Taos::Store {

QStringList sectionIds()
{
    return {u"discover"_s, u"apps"_s, u"webapps"_s, u"models"_s, u"themes"_s, u"plugins"_s, u"installed"_s, u"updates"_s};
}

QString sizeText(double mb)
{
    if (mb <= 0)
        return {};
    if (mb < 1024)
        return u"%1 MB"_s.arg(qRound(mb));
    const double gb = mb / 1024.0;
    return u"%1 GB"_s.arg(gb, 0, 'f', gb < 10 ? 1 : 0);
}

QString kindOfType(const QString &type)
{
    if (type == u"model"_s)
        return u"model"_s;
    if (type == u"agent-framework"_s)
        return u"agent"_s;
    if (type == u"plugin"_s)
        return u"plugin"_s;
    if (type == u"mcp"_s)
        return u"mcp"_s;
    if (type == u"webapp"_s)
        return u"webapp"_s;
    return u"app"_s;
}

QString sectionOfKind(const QString &kind)
{
    if (kind == u"model"_s)
        return u"models"_s;
    if (kind == u"plugin"_s || kind == u"mcp"_s)
        return u"plugins"_s;
    if (kind == u"webapp"_s)
        return u"webapps"_s;
    return u"apps"_s;
}

QString fitFromTiers(const QJsonObject &tiers, const QString &profileId)
{
    if (tiers.isEmpty() || profileId.isEmpty())
        return {};
    if (!tiers.contains(profileId))
        return u"red"_s;
    const QJsonValue t = tiers.value(profileId);
    if (t.isString()) {
        const QString s = t.toString();
        if (s == u"unsupported"_s)
            return u"red"_s;
        if (s == u"full"_s || s == u"optimal"_s)
            return u"green"_s;
        return u"yellow"_s;
    }
    if (t.isObject())
        return u"green"_s;
    return u"yellow"_s;
}

QString fitText(const QString &fit)
{
    if (fit == u"green"_s)
        return QObject::tr("Runs well on this device");
    if (fit == u"yellow"_s)
        return QObject::tr("May run slowly on this device");
    if (fit == u"red"_s)
        return QObject::tr("Does not fit this device");
    return {};
}

namespace {

QString letterOf(const QString &name)
{
    for (QChar c : name)
        if (c.isLetterOrNumber())
            return QString(c.toUpper());
    return u"?"_s;
}

QString runtimeText(const QString &runtime, const QString &method, bool needsDocker)
{
    if (runtime == u"either"_s)
        return QObject::tr("Docker or LXC container");
    if (needsDocker)
        return QObject::tr("Needs Docker");
    if (runtime == u"lxc"_s || method == u"lxc"_s)
        return QObject::tr("LXC container");
    if (method == u"pip"_s)
        return QObject::tr("Python package");
    if (method == u"script"_s)
        return QObject::tr("Install script");
    if (method == u"download"_s)
        return QObject::tr("Model download");
    return {};
}

} // namespace

QVariantMap normalizeServerEntry(const QJsonObject &o)
{
    QVariantMap e;
    const QString id = o.value(u"id"_s).toString();
    const QString type = o.value(u"type"_s).toString(u"plugin"_s);
    const QString kind = kindOfType(type);
    const QString name = o.value(u"name"_s).toString(id);
    e[u"id"_s] = id;
    e[u"name"_s] = name;
    e[u"type"_s] = type;
    e[u"kind"_s] = kind;
    e[u"section"_s] = sectionOfKind(kind);
    e[u"category"_s] = o.value(u"category"_s).toString();
    e[u"version"_s] = o.value(u"version"_s).toString();
    e[u"description"_s] = o.value(u"description"_s).toString();
    e[u"tagline"_s] = o.value(u"tagline"_s).toString();
    e[u"icon"_s] = o.value(u"icon"_s).toString();
    e[u"letter"_s] = letterOf(name);
    const QString state = o.value(u"state"_s).toString();
    const bool installed = o.value(u"installed"_s).toBool() || state == u"installed"_s || state == u"running"_s;
    e[u"installed"_s] = installed;
    e[u"state"_s] = state;
    e[u"updateAvailable"_s] = o.value(u"update_available"_s).toBool() || o.value(u"upstream_update_available"_s).toBool();
    e[u"upstreamVersion"_s] = o.value(u"upstream_version"_s).toString();
    e[u"pinnedVersion"_s] = o.value(u"upstream_pinned_version"_s).toString();
    const QString method = o.value(u"install_method"_s).toString();
    const QString runtime = o.value(u"runtime"_s).toString();   // lxc | docker | either, when the manifest has it
    e[u"installMethod"_s] = method;
    e[u"runtime"_s] = runtime;
    // Docker is needed when the manifest says so, or says nothing and installs through docker.
    const bool needsDocker = runtime == u"docker"_s || (runtime.isEmpty() && method == u"docker"_s);
    e[u"needsDocker"_s] = needsDocker;
    e[u"runtimeText"_s] = runtimeText(runtime, method, needsDocker);
    const QJsonObject req = o.value(u"requires"_s).toObject();
    e[u"ramMb"_s] = req.value(u"ram_mb"_s).toInt();
    e[u"diskMb"_s] = req.value(u"disk_mb"_s).toInt();
    double sizeMb = req.value(u"disk_mb"_s).toDouble();
    QVariantList variants;
    for (const QJsonValue &v : o.value(u"variants"_s).toArray()) {
        const QJsonObject vo = v.toObject();
        QVariantMap vm;
        vm[u"id"_s] = vo.value(u"id"_s).toString();
        vm[u"name"_s] = vo.value(u"name"_s).toString(vo.value(u"id"_s).toString());
        vm[u"sizeMb"_s] = vo.value(u"size_mb"_s).toDouble();
        vm[u"sizeText"_s] = sizeText(vo.value(u"size_mb"_s).toDouble());
        vm[u"backends"_s] = vo.value(u"backend"_s).toVariant();
        variants << vm;
    }
    if (kind == u"model"_s && !variants.isEmpty()) {
        double smallest = 0;
        for (const QVariant &v : variants) {
            const double s = v.toMap().value(u"sizeMb"_s).toDouble();
            if (s > 0 && (smallest == 0 || s < smallest))
                smallest = s;
        }
        if (smallest > 0)
            sizeMb = smallest;
    }
    e[u"variants"_s] = variants;
    e[u"sizeText"_s] = sizeText(sizeMb);
    e[u"license"_s] = o.value(u"license"_s).toString();
    e[u"weightsLicense"_s] = o.value(u"weights_license"_s).toString();
    e[u"licenseClass"_s] = o.value(u"license_class"_s).toString();
    e[u"repo"_s] = o.value(u"repo"_s).toString();
    e[u"stars"_s] = o.value(u"stars"_s).toInt();
    e[u"webapp"_s] = kind == u"webapp"_s;
    if (kind == u"webapp"_s) {
        const QString url = o.value(u"url"_s).toString(o.value(u"homepage"_s).toString());
        e[u"url"_s] = url;
        e[u"host"_s] = QUrl(url).host();
    }
    e[u"fit"_s] = QString();
    e[u"fitText"_s] = QString();
    e[u"tiers"_s] = o.value(u"hardware_tiers"_s).toObject().toVariantMap();
    e[u"source"_s] = u"controller"_s;
    return e;
}

QVariantMap normalizeWebappEntry(const QJsonObject &o)
{
    QJsonObject s = o;
    s[u"type"_s] = u"webapp"_s;
    if (!s.contains(u"category"_s) && o.value(u"categories"_s).isArray() && !o.value(u"categories"_s).toArray().isEmpty())
        s[u"category"_s] = o.value(u"categories"_s).toArray().first().toString();
    QVariantMap e = normalizeServerEntry(s);
    e[u"source"_s] = u"builtin"_s;
    e[u"categories"_s] = o.value(u"categories"_s).toVariant();
    e[u"runtimeText"_s] = QObject::tr("Opens in its own browser window");
    return e;
}

// ---------------------------------------------------------------- Catalog

void Catalog::setServer(const QJsonArray &entries)
{
    m_serverArr = entries;
    rebuild();
}

void Catalog::setBuiltinWebapps(const QJsonArray &apps)
{
    m_webArr = apps;
    rebuild();
}

void Catalog::setProfile(const QString &profileId)
{
    m_profile = profileId;
    rebuild();
}

void Catalog::setModelFit(const QJsonArray &models)
{
    m_modelFit.clear();
    for (const QJsonValue &v : models) {
        const QJsonObject m = v.toObject();
        QString fit = m.value(u"compatibility"_s).toString();
        if (!fit.isEmpty())
            m_modelFit.insert(m.value(u"id"_s).toString(), fit);
    }
    rebuild();
}

void Catalog::setInstalled(const QSet<QString> &ids)
{
    m_installed = ids;
    m_forceInstalled.clear();
    m_forceRemoved.clear();
    rebuild();
}

void Catalog::setInstanceCounts(const QHash<QString, int> &counts)
{
    m_instances = counts;
    rebuild();
}

void Catalog::markInstalled(const QString &id, bool installed)
{
    if (installed) {
        m_forceInstalled.insert(id);
        m_forceRemoved.remove(id);
    } else {
        m_forceRemoved.insert(id);
        m_forceInstalled.remove(id);
    }
    rebuild();
}

void Catalog::rebuild()
{
    m_entries.clear();
    m_index.clear();
    auto add = [&](QVariantMap e) {
        const QString id = e.value(u"id"_s).toString();
        if (id.isEmpty())
            return;
        const int at = m_index.value(id, -1);
        if (at >= 0) {
            // a controller entry of kind webapp replaces the built-in one; otherwise the first wins
            if (e.value(u"source"_s).toString() == u"controller"_s && m_entries[at].value(u"source"_s).toString() == u"builtin"_s)
                m_entries[at] = e;
            return;
        }
        m_index.insert(id, m_entries.size());
        m_entries << e;
    };
    for (const QJsonValue &v : std::as_const(m_serverArr)) {
        const QJsonObject o = v.toObject();
        QVariantMap e = o.value(u"type"_s).toString() == u"webapp"_s ? normalizeWebappEntry(o) : normalizeServerEntry(o);
        if (o.value(u"type"_s).toString() == u"webapp"_s)
            e[u"source"_s] = u"controller"_s;
        add(e);
    }
    for (const QJsonValue &v : std::as_const(m_webArr))
        add(normalizeWebappEntry(v.toObject()));
    for (QVariantMap &e : m_entries) {
        const QString id = e.value(u"id"_s).toString();
        const QString kind = e.value(u"kind"_s).toString();
        if (kind == u"webapp"_s) {
            const int n = m_instances.value(id);
            e[u"instances"_s] = n;
            e[u"installed"_s] = n > 0;
        } else {
            bool inst = e.value(u"installed"_s).toBool() || m_installed.contains(id);
            if (m_forceInstalled.contains(id))
                inst = true;
            if (m_forceRemoved.contains(id))
                inst = false;
            e[u"installed"_s] = inst;
            e[u"updateAvailable"_s] = inst && e.value(u"updateAvailable"_s).toBool();
        }
        QString fit = m_modelFit.value(id);
        if (fit.isEmpty() && kind != u"webapp"_s) {
            QJsonObject tiers = QJsonObject::fromVariantMap(e.value(u"tiers"_s).toMap());
            fit = fitFromTiers(tiers, m_profile);
        }
        e[u"fit"_s] = fit;
        e[u"fitText"_s] = fitText(fit);
    }
}

QVariantList Catalog::all() const
{
    QVariantList out;
    for (const QVariantMap &e : m_entries)
        out << e;
    return out;
}

QVariantMap Catalog::entry(const QString &id) const
{
    const int at = m_index.value(id, -1);
    return at < 0 ? QVariantMap() : m_entries.at(at);
}

int Catalog::score(const QVariantMap &e, const QStringList &words)
{
    if (words.isEmpty())
        return 1;
    const QString name = e.value(u"name"_s).toString().toLower();
    const QString id = e.value(u"id"_s).toString().toLower();
    const QString cat = (e.value(u"category"_s).toString() + u' ' + e.value(u"type"_s).toString()).toLower();
    const QString desc = (e.value(u"description"_s).toString() + u' ' + e.value(u"tagline"_s).toString()).toLower();
    int total = 0;
    for (const QString &w : words) {
        if (name.startsWith(w))
            total += 100;
        else if (name.contains(u' ' + w))
            total += 80;
        else if (name.contains(w))
            total += 60;
        else if (id.contains(w))
            total += 40;
        else if (cat.contains(w))
            total += 30;
        else if (desc.contains(w))
            total += 15;
        else
            return 0;   // every word must be found somewhere
    }
    return total;
}

static QString chipKey(const QVariantMap &e)
{
    const QString kind = e.value(u"kind"_s).toString();
    if (kind == u"agent"_s)
        return u"agent-framework"_s;
    QString c = e.value(u"category"_s).toString();
    if (c.isEmpty())
        c = e.value(u"type"_s).toString();
    return c;
}

QVariantList Catalog::section(const QString &sec, const QString &query, const QString &chip) const
{
    const QStringList words = query.toLower().simplified().split(u' ', Qt::SkipEmptyParts);
    struct Hit { int score; int order; QVariantMap e; };
    QList<Hit> hits;
    int order = 0;
    for (const QVariantMap &e : m_entries) {
        ++order;
        const QString s = e.value(u"section"_s).toString();
        if (sec == u"installed"_s) {
            if (!e.value(u"installed"_s).toBool())
                continue;
        } else if (sec == u"updates"_s) {
            if (!e.value(u"updateAvailable"_s).toBool())
                continue;
        } else if (s != sec) {
            continue;
        }
        if (!chip.isEmpty() && chip != u"all"_s && chipKey(e) != chip)
            continue;
        const int sc = score(e, words);
        if (sc <= 0)
            continue;
        hits.append({sc, order, e});
    }
    const bool searching = !words.isEmpty();
    std::stable_sort(hits.begin(), hits.end(), [&](const Hit &a, const Hit &b) {
        if (searching && a.score != b.score)
            return a.score > b.score;
        return QString::localeAwareCompare(a.e.value(u"name"_s).toString(), b.e.value(u"name"_s).toString()) < 0;
    });
    QVariantList out;
    for (const Hit &h : hits)
        out << h.e;
    return out;
}

QVariantList Catalog::search(const QString &query) const
{
    const QStringList words = query.toLower().simplified().split(u' ', Qt::SkipEmptyParts);
    QList<QPair<int, QVariantMap>> hits;
    for (const QVariantMap &e : m_entries) {
        const int sc = score(e, words);
        if (sc > 0)
            hits.append({sc, e});
    }
    std::stable_sort(hits.begin(), hits.end(), [](const auto &a, const auto &b) {
        if (a.first != b.first)
            return a.first > b.first;
        return QString::localeAwareCompare(a.second.value(u"name"_s).toString(), b.second.value(u"name"_s).toString()) < 0;
    });
    QVariantList out;
    for (const auto &h : hits)
        out << h.second;
    return out;
}

QVariantList Catalog::chips(const QString &sec) const
{
    QHash<QString, int> counts;
    int total = 0;
    for (const QVariantMap &e : m_entries) {
        if (e.value(u"section"_s).toString() != sec)
            continue;
        ++total;
        const QString k = chipKey(e);
        if (!k.isEmpty())
            counts[k]++;
    }
    QList<QPair<QString, int>> sorted;
    for (auto it = counts.cbegin(); it != counts.cend(); ++it)
        sorted.append({it.key(), it.value()});
    std::sort(sorted.begin(), sorted.end(), [](const auto &a, const auto &b) { return a.second != b.second ? a.second > b.second : a.first < b.first; });
    QVariantList out;
    out << QVariantMap{{u"id"_s, u"all"_s}, {u"label"_s, QObject::tr("All")}, {u"count"_s, total}};
    for (const auto &p : sorted) {
        if (out.size() > 9)
            break;
        QString label = p.first;
        label.replace(u'-', u' ');
        if (!label.isEmpty())
            label[0] = label[0].toUpper();
        out << QVariantMap{{u"id"_s, p.first}, {u"label"_s, label}, {u"count"_s, p.second}};
    }
    return out;
}

int Catalog::count(const QString &sec) const
{
    return section(sec).size();
}

QVariantList Catalog::discover() const
{
    QVariantList groups;
    auto group = [&](const QString &id, const QString &title, const QVariantList &items, int max) {
        if (items.isEmpty())
            return;
        QVariantList cut = items.mid(0, max);
        groups << QVariantMap{{u"id"_s, id}, {u"title"_s, title}, {u"items"_s, cut}, {u"total"_s, items.size()}};
    };
    QVariantList web = section(u"webapps"_s);
    // YouTube first: the entry most people reach for
    std::stable_partition(web.begin(), web.end(), [](const QVariant &v) { return v.toMap().value(u"id"_s).toString() == u"youtube"_s; });
    group(u"webapps"_s, QObject::tr("Web apps"), web, 8);
    QVariantList apps = section(u"apps"_s);
    std::stable_sort(apps.begin(), apps.end(), [](const QVariant &a, const QVariant &b) {
        return a.toMap().value(u"stars"_s).toInt() > b.toMap().value(u"stars"_s).toInt();
    });
    group(u"apps"_s, QObject::tr("Popular apps"), apps, 8);
    QVariantList models;
    for (const QVariant &v : section(u"models"_s))
        if (v.toMap().value(u"fit"_s).toString() == u"green"_s)
            models << v;
    group(u"models"_s, QObject::tr("Models that run well here"), models, 6);
    group(u"plugins"_s, QObject::tr("Plugins and tools"), section(u"plugins"_s), 6);
    return groups;
}

} // namespace Taos::Store
