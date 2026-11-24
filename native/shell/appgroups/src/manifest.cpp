// SPDX-License-Identifier: AGPL-3.0-or-later
#include "manifest.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QRegularExpression>
#include <QUrl>

using namespace Qt::StringLiterals;

namespace TaosAppGroups {

QStringList allowedHosts()
{
    return {u"edgedl.me.gvt1.com"_s,        u"us-central1-apt.pkg.dev"_s, u"packages.mozilla.org"_s,
            u"deb.opera.com"_s,             u"brave-browser-apt-release.s3.brave.com"_s,
            u"repo.vivaldi.com"_s,          u"deb.librewolf.net"_s,       u"dl.google.com"_s,
            u"update.librewolf.net"_s};
}

namespace {

bool fail(QString *e, const QString &m)
{
    if (e)
        *e = m;
    return false;
}

bool okId(const QString &s) { return QRegularExpression(u"^[a-z][a-z0-9-]{1,40}$"_s).match(s).hasMatch(); }
bool okPkg(const QString &s) { return QRegularExpression(u"^[a-z0-9][a-z0-9+.-]{1,80}$"_s).match(s).hasMatch(); }
bool okToken(const QString &s) { return QRegularExpression(u"^[A-Za-z0-9._+/:=-]{1,200}$"_s).match(s).hasMatch(); }

bool okUrl(const QString &s, QString *e, const QString &what)
{
    const QUrl u(s, QUrl::StrictMode);
    if (!u.isValid() || u.scheme() != u"https"_s || u.host().isEmpty() || !u.userInfo().isEmpty())
        return fail(e, u"%1 must be an https URL"_s.arg(what));
    if (!allowedHosts().contains(u.host()))
        return fail(e, u"%1 host %2 is not on the allowlist"_s.arg(what, u.host()));
    return true;
}

QStringList strList(const QJsonValue &v)
{
    QStringList l;
    for (const QJsonValue &x : v.toArray())
        l << x.toString();
    return l;
}

bool parseStep(const QJsonObject &o, const QString &appId, Step *s, QString *e)
{
    const QString op = o.value(u"op"_s).toString();
    s->op = op;
    s->args = o;
    if (op == u"apt-source"_s) {
        const QString name = o.value(u"name"_s).toString();
        if (!okId(name) || !okUrl(o.value(u"uri"_s).toString(), e, u"apt-source uri"_s))
            return e && !e->isEmpty() ? false : fail(e, u"apt-source needs a valid name"_s);
        if (!okUrl(o.value(u"key_url"_s).toString(), e, u"apt-source key_url"_s))
            return false;
        if (!QRegularExpression(u"^[0-9A-F]{40}$"_s).match(o.value(u"key_fingerprint"_s).toString()).hasMatch())
            return fail(e, u"apt-source key_fingerprint must be 40 upper case hex characters"_s);
        if (!okToken(o.value(u"suite"_s).toString()))
            return fail(e, u"apt-source suite is invalid"_s);
        const QStringList comps = strList(o.value(u"components"_s));
        if (comps.isEmpty())
            return fail(e, u"apt-source needs components"_s);
        for (const QString &c : comps)
            if (!okToken(c))
                return fail(e, u"apt-source component is invalid"_s);
        return true;
    }
    if (op == u"apt-install"_s) {
        const QStringList p = strList(o.value(u"packages"_s));
        if (p.isEmpty())
            return fail(e, u"apt-install needs packages"_s);
        for (const QString &x : p)
            if (!okPkg(x))
                return fail(e, u"apt-install package name %1 is not allowed"_s.arg(x));
        return true;
    }
    if (op == u"download-tar"_s || op == u"download-deb"_s) {
        if (!okUrl(o.value(u"url"_s).toString(), e, op + u" url"_s))
            return false;
        if (!QRegularExpression(u"^[0-9a-f]{64}$"_s).match(o.value(u"sha256"_s).toString()).hasMatch())
            return fail(e, op + u" needs a sha256 (64 lower case hex characters)"_s);
        if (op == u"download-tar"_s) {
            const QString dest = o.value(u"dest"_s).toString();
            if (dest != u"/opt/"_s + appId)
                return fail(e, u"download-tar dest must be /opt/%1"_s.arg(appId));
            const int strip = o.value(u"strip"_s).toInt(0);
            if (strip < 0 || strip > 2)
                return fail(e, u"download-tar strip must be 0 to 2"_s);
        }
        return true;
    }
    if (op == u"symlink"_s) {
        const QString t = o.value(u"target"_s).toString(), l = o.value(u"link"_s).toString();
        if (!t.startsWith(u"/opt/"_s + appId + u"/"_s) || t.contains(u".."_s) || t.contains(u' '))
            return fail(e, u"symlink target must be inside /opt/%1 and have no spaces"_s.arg(appId));
        if (!QRegularExpression(u"^/usr/local/bin/[a-z0-9][a-z0-9._-]{0,60}$"_s).match(l).hasMatch())
            return fail(e, u"symlink link must be /usr/local/bin/<name>"_s);
        return true;
    }
    if (op == u"suid-helper"_s) {
        const QString p = o.value(u"path"_s).toString();
        if (!p.startsWith(u"/opt/"_s + appId + u"/"_s) || p.contains(u".."_s) || p.contains(u' '))
            return fail(e, u"suid-helper path must be inside /opt/%1 and have no spaces"_s.arg(appId));
        return true;
    }
    return fail(e, u"unknown recipe step \"%1\" (allowed: apt-source, apt-install, download-tar, download-deb, symlink, suid-helper)"_s.arg(op));
}

} // namespace

bool parseManifest(const QByteArray &json, Manifest *out, QString *error)
{
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (!d.isObject())
        return fail(error, u"manifest is not a JSON object: %1"_s.arg(pe.errorString()));
    const QJsonObject o = d.object();
    Manifest m;
    m.raw = o;
    if (o.value(u"kind"_s).toString() != u"container-desktop-app"_s || o.value(u"schema"_s).toInt() != 1)
        return fail(error, u"manifest kind must be container-desktop-app, schema 1"_s);
    m.id = o.value(u"id"_s).toString();
    if (!okId(m.id))
        return fail(error, u"manifest id is invalid"_s);
    m.name = o.value(u"name"_s).toString();
    m.description = o.value(u"description"_s).toString();
    m.publisher = o.value(u"publisher"_s).toString();
    m.homepage = o.value(u"homepage"_s).toString();
    m.licence = o.value(u"licence"_s).toString();
    m.version = o.value(u"version"_s).toString();
    m.termsNote = o.value(u"terms"_s).toObject().value(u"note"_s).toString();
    m.termsUrl = o.value(u"terms"_s).toObject().value(u"url"_s).toString();
    if (m.name.isEmpty() || m.description.isEmpty() || m.publisher.isEmpty() || m.licence.isEmpty() || m.termsNote.isEmpty())
        return fail(error, u"manifest needs name, description, publisher, licence and a terms note"_s);
    if (!m.homepage.startsWith(u"https://"_s))
        return fail(error, u"homepage must be https"_s);
    m.iconGlyph = o.value(u"icon"_s).toObject().value(u"glyph"_s).toString(u"app"_s);
    if (m.iconGlyph != u"code"_s && m.iconGlyph != u"browser"_s && m.iconGlyph != u"app"_s)
        return fail(error, u"icon glyph must be code, browser or app"_s);
    m.categories = strList(o.value(u"categories"_s));
    m.arch = strList(o.value(u"arch"_s));
    if (m.arch.isEmpty())
        return fail(error, u"manifest needs arch"_s);
    m.mimeTypes = strList(o.value(u"mime_types"_s));
    const QJsonObject launch = o.value(u"launch"_s).toObject();
    m.launchArgv = strList(launch.value(u"argv"_s));
    m.launchEnv = launch.value(u"env"_s).toObject();
    if (m.launchArgv.isEmpty() || !m.launchArgv.first().startsWith(u'/'))
        return fail(error, u"launch.argv must be an argument list that starts with an absolute path"_s);
    for (const QString &a : m.launchArgv)
        if (a.contains(u'\n') || a.contains(u'\0'))
            return fail(error, u"launch.argv has a bad character"_s);
    for (auto it = m.launchEnv.begin(); it != m.launchEnv.end(); ++it)
        if (!QRegularExpression(u"^[A-Z][A-Z0-9_]{0,40}$"_s).match(it.key()).hasMatch() || !it.value().isString())
            return fail(error, u"launch.env has an invalid entry"_s);
    m.windowAppId = o.value(u"window"_s).toObject().value(u"app_id"_s).toString();
    if (m.windowAppId.isEmpty() || !okToken(m.windowAppId))
        return fail(error, u"window.app_id is required"_s);
    m.binary = o.value(u"binary"_s).toString();
    if (!m.binary.startsWith(u'/'))
        return fail(error, u"binary must be an absolute path the install leaves behind"_s);
    const QJsonObject g = o.value(u"grants"_s).toObject();
    static const QStringList known = {u"network"_s, u"gpu"_s, u"audio"_s, u"documents"_s, u"downloads"_s, u"nesting"_s, u"camera"_s, u"microphone"_s};
    for (auto it = g.begin(); it != g.end(); ++it)
        if (!known.contains(it.key()) || !it.value().isBool())
            return fail(error, u"unknown grant \"%1\""_s.arg(it.key()));
    m.grants.network = g.value(u"network"_s).toBool();
    m.grants.gpu = g.value(u"gpu"_s).toBool();
    m.grants.audio = g.value(u"audio"_s).toBool();
    m.grants.documents = g.value(u"documents"_s).toBool();
    m.grants.downloads = g.value(u"downloads"_s).toBool();
    m.grants.nesting = g.value(u"nesting"_s).toBool();
    m.grants.camera = g.value(u"camera"_s).toBool();
    m.grants.microphone = g.value(u"microphone"_s).toBool();
    const QJsonArray rec = o.value(u"recipe"_s).toArray();
    if (rec.isEmpty())
        return fail(error, u"manifest needs a recipe"_s);
    for (const QJsonValue &v : rec) {
        Step s;
        QString e;
        if (!parseStep(v.toObject(), m.id, &s, &e))
            return fail(error, e);
        m.recipe.append(s);
    }
    *out = m;
    return true;
}

QJsonObject manifestSummary(const Manifest &m)
{
    QJsonObject g;
    g[u"network"_s] = m.grants.network;
    g[u"gpu"_s] = m.grants.gpu;
    g[u"audio"_s] = m.grants.audio;
    g[u"documents"_s] = m.grants.documents;
    g[u"downloads"_s] = m.grants.downloads;
    g[u"nesting"_s] = m.grants.nesting;
    g[u"camera"_s] = m.grants.camera;
    g[u"microphone"_s] = m.grants.microphone;
    QJsonObject o;
    o[u"id"_s] = m.id;
    o[u"name"_s] = m.name;
    o[u"type"_s] = u"container-desktop-app"_s;
    o[u"description"_s] = m.description;
    o[u"publisher"_s] = m.publisher;
    o[u"homepage"_s] = m.homepage;
    o[u"licence"_s] = m.licence;
    o[u"terms_note"_s] = m.termsNote;
    o[u"terms_url"_s] = m.termsUrl;
    o[u"version"_s] = m.version;
    o[u"icon_glyph"_s] = m.iconGlyph;
    o[u"categories"_s] = QJsonArray::fromStringList(m.categories);
    o[u"arch"_s] = QJsonArray::fromStringList(m.arch);
    o[u"window_app_id"_s] = m.windowAppId;
    o[u"grants"_s] = g;
    return o;
}

} // namespace TaosAppGroups
