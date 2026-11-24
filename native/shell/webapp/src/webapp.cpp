#include "webapp.h"

#include <QBuffer>
#include <QCoreApplication>
#include <QDir>
#include <QDirIterator>
#include <QEventLoop>
#include <QFile>
#include <QFileInfo>
#include <QImage>
#include <QJsonDocument>
#include <QLockFile>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QProcess>
#include <QProcessEnvironment>
#include <QRandomGenerator>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSet>
#include <QStandardPaths>
#include <QThread>
#include <QTimer>

#include <csignal>
#include <cerrno>
#include <unistd.h>

using namespace Qt::StringLiterals;

namespace TaosWebapp {

// ---------------------------------------------------------------- Instance

QString expectedAppId(const QUrl &url, const QString &profileDir)
{
    QString path = url.path(QUrl::FullyEncoded);
    if (path.isEmpty())
        path = u"/"_s;
    QString name = url.host(QUrl::FullyEncoded).toLower() + u'_' + path;   // web_app::GenerateApplicationNameFromURL
    name.replace(u'/', u'_');
    for (const QChar c : {u'\\', u':', u'*', u'?', u'"', u'<', u'>', u'|'})   // ReplaceIllegalCharactersInPath
        name.replace(c, u'_');
    return u"chrome-"_s + name + u'-' + (profileDir.isEmpty() ? u"Default"_s : profileDir);
}

QJsonObject Instance::toJson() const
{
    QJsonObject o;
    o[u"id"_s] = id;
    o[u"name"_s] = name;
    o[u"url"_s] = url;
    o[u"icon"_s] = icon;
    o[u"source"_s] = source;
    o[u"browser"_s] = browser;
    o[u"profile_dir"_s] = profile;
    o[u"created"_s] = created.isValid() ? created.toUTC().toString(Qt::ISODate) : QString();
    o[u"last_used"_s] = lastUsed.isValid() ? lastUsed.toUTC().toString(Qt::ISODate) : QString();
    o[u"app_id"_s] = appId();
    return o;
}

Instance Instance::fromJson(const QJsonObject &o)
{
    Instance i;
    i.id = o.value(u"id"_s).toString();
    i.name = o.value(u"name"_s).toString();
    i.url = o.value(u"url"_s).toString();
    i.icon = o.value(u"icon"_s).toString();
    i.source = o.value(u"source"_s).toString(u"custom"_s);
    i.browser = o.value(u"browser"_s).toString(u"auto"_s);
    i.profile = o.value(u"profile_dir"_s).toString(u"Default"_s);   // instances made before this field used Default
    if (i.profile.isEmpty() || i.profile.contains(u'/') || i.profile.contains(u".."_s))
        i.profile = u"Default"_s;
    i.created = QDateTime::fromString(o.value(u"created"_s).toString(), Qt::ISODate);
    i.lastUsed = QDateTime::fromString(o.value(u"last_used"_s).toString(), Qt::ISODate);
    return i;
}

// ---------------------------------------------------------------- Options

Options Options::fromEnvironment()
{
    Options o;
    QString data = qEnvironmentVariable("XDG_DATA_HOME");
    if (data.isEmpty())
        data = QDir::homePath() + u"/.local/share"_s;
    o.root = data + u"/taos/webapps"_s;
    o.applicationsDir = data + u"/applications"_s;
    if (qEnvironmentVariableIsSet("TAOS_WEBAPP_EXEC"))
        o.execName = qEnvironmentVariable("TAOS_WEBAPP_EXEC");
    return o;
}

// ---------------------------------------------------------------- Chromium backend

QStringList ChromiumBackend::candidates() const
{
    return {u"chromium"_s, u"chromium-browser"_s, u"google-chrome"_s, u"google-chrome-stable"_s,
            u"brave-browser"_s, u"brave"_s};
}

QString ChromiumBackend::probe(const QString &preferred) const
{
    // TAOS_WEBAPP_BROWSER pins one binary (a name on PATH or a path): tests and a person who wants Brave.
    const QString pinned = qEnvironmentVariable("TAOS_WEBAPP_BROWSER");
    QStringList names;
    if (!pinned.isEmpty())
        names << pinned;
    else {
        if (!preferred.isEmpty() && preferred != u"auto"_s)
            names << preferred;
        names << candidates();
    }
    for (const QString &n : std::as_const(names)) {
        if (n.contains(u'/')) {
            const QFileInfo fi(n);
            if (fi.isFile() && fi.isExecutable())
                return fi.absoluteFilePath();
            continue;
        }
        const QString p = QStandardPaths::findExecutable(n);
        if (!p.isEmpty())
            return p;
    }
    return {};
}

LaunchSpec ChromiumBackend::launchSpec(const QString &program, const Instance &inst, const QString &profileDir) const
{
    const QString appId = inst.appId();
    QStringList a;
    a << u"--app="_s + inst.url
      << u"--user-data-dir="_s + profileDir
      << u"--profile-directory="_s + inst.profile
      << u"--class="_s + appId
      << u"--name="_s + appId
      << (qEnvironmentVariableIsSet("TAOS_WEBAPP_OZONE") ? u"--ozone-platform="_s + qEnvironmentVariable("TAOS_WEBAPP_OZONE")
                                                        : u"--ozone-platform=wayland"_s)
      << u"--no-first-run"_s
      << u"--no-default-browser-check"_s;
    return {program, a};
}

static bool pidAlive(qint64 pid)
{
    if (pid <= 1)
        return false;
    return ::kill(static_cast<pid_t>(pid), 0) == 0 || errno == EPERM;
}

qint64 ChromiumBackend::profileOwnerPid(const QString &profileDir) const
{
    // Chromium keeps SingletonLock as a symlink to "<host>-<pid>" while a browser owns the profile.
    const QString target = QFile::symLinkTarget(profileDir + u"/SingletonLock"_s);
    QString t = target;
    if (t.isEmpty()) {
        // QFile::symLinkTarget resolves relative to the link; a dangling link gives "". Read it raw.
        char buf[256];
        const QByteArray path = QFile::encodeName(profileDir + u"/SingletonLock"_s);
        const ssize_t n = ::readlink(path.constData(), buf, sizeof buf - 1);
        if (n <= 0)
            return 0;
        t = QString::fromLocal8Bit(buf, static_cast<int>(n));
    }
    const int dash = t.lastIndexOf(u'-');
    if (dash < 0)
        return 0;
    bool ok = false;
    const qint64 pid = t.mid(dash + 1).toLongLong(&ok);
    return ok && pidAlive(pid) ? pid : 0;
}

// ---------------------------------------------------------------- helpers

namespace {

const QRegularExpression &idRe()
{
    static const QRegularExpression re(u"^[a-z0-9]{10}$"_s);
    return re;
}

QString cleanName(const QString &in)
{
    QString s;
    for (QChar c : in)
        s += c.isPrint() ? c : QChar(u' ');
    s = s.simplified();
    if (s.size() > 80)
        s = s.left(80).trimmed();
    return s;
}

QString isoNow(const std::function<QDateTime()> &clock)
{
    return (clock ? clock() : QDateTime::currentDateTimeUtc()).toUTC().toString(Qt::ISODate);
}

bool writeFileAtomic(const QString &path, const QByteArray &data, QString *error)
{
    QSaveFile f(path);
    if (!f.open(QIODevice::WriteOnly)) {
        if (error)
            *error = f.errorString();
        return false;
    }
    f.write(data);
    if (!f.commit()) {
        if (error)
            *error = f.errorString();
        return false;
    }
    return true;
}

QString xmlEscape(const QString &s)
{
    QString o = s;
    o.replace(u'&', u"&amp;"_s).replace(u'<', u"&lt;"_s).replace(u'>', u"&gt;"_s).replace(u'"', u"&quot;"_s);
    return o;
}

QString desktopEscape(const QString &s)
{
    QString o;
    for (QChar c : s) {
        if (c == u'\\')
            o += u"\\\\"_s;
        else if (c == u'\n' || c == u'\r' || c == u'\t')
            o += u' ';
        else
            o += c;
    }
    return o;
}

QString desktopExecQuote(const QString &s)
{
    if (!s.contains(QRegularExpression(u"[\\s\"'`$\\\\<>~|&;*?#()]"_s)))
        return s;
    QString o = u"\""_s;
    for (QChar c : s) {
        if (c == u'"' || c == u'`' || c == u'$' || c == u'\\')
            o += u'\\';
        o += c;
    }
    return o + u"\""_s;
}

qint64 dirSize(const QString &dir)
{
    qint64 total = 0;
    QDirIterator it(dir, QDir::Files | QDir::Hidden | QDir::System | QDir::NoSymLinks, QDirIterator::Subdirectories);
    while (it.hasNext()) {
        it.next();
        total += it.fileInfo().size();
    }
    return total;
}

const QSet<QString> &cloneSkip()
{
    static const QSet<QString> s = {u"SingletonLock"_s, u"SingletonSocket"_s, u"SingletonCookie"_s, u"lockfile"_s,
                                    u"Cache"_s, u"Code Cache"_s, u"GPUCache"_s, u"GrShaderCache"_s, u"ShaderCache"_s,
                                    u"DawnGraphiteCache"_s, u"DawnWebGPUCache"_s, u"Crashpad"_s, u"component_crx_cache"_s};
    return s;
}

/// Chromium's `Local State` names profiles by directory: info_cache, last_used, last_active_profiles, profiles_order.
void patchLocalState(const QString &path, const QString &from, const QString &to)
{
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly))
        return;
    QJsonParseError pe;
    QJsonObject root = QJsonDocument::fromJson(f.readAll(), &pe).object();
    f.close();
    if (pe.error != QJsonParseError::NoError || !root.contains(u"profile"_s))
        return;
    QJsonObject prof = root.value(u"profile"_s).toObject();
    QJsonObject cache = prof.value(u"info_cache"_s).toObject();
    if (cache.contains(from)) {
        cache.insert(to, cache.value(from));
        cache.remove(from);
        prof.insert(u"info_cache"_s, cache);
    }
    if (prof.value(u"last_used"_s).toString() == from)
        prof.insert(u"last_used"_s, to);
    for (const QString &k : {u"last_active_profiles"_s, u"profiles_order"_s}) {
        QJsonArray a = prof.value(k).toArray();
        bool changed = false;
        for (int i = 0; i < a.size(); ++i)
            if (a.at(i).toString() == from) {
                a[i] = to;
                changed = true;
            }
        if (changed)
            prof.insert(k, a);
    }
    root.insert(u"profile"_s, prof);
    writeFileAtomic(path, QJsonDocument(root).toJson(QJsonDocument::Compact), nullptr);
}

bool copyTree(const QString &src, const QString &dst, QString *error)
{
    if (!QDir().mkpath(dst)) {
        *error = u"cannot create %1"_s.arg(dst);
        return false;
    }
    QFile::setPermissions(dst, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    const QDir d(src);
    const auto entries = d.entryInfoList(QDir::AllEntries | QDir::NoDotAndDotDot | QDir::Hidden | QDir::System);
    for (const QFileInfo &fi : entries) {
        if (fi.isSymLink() || cloneSkip().contains(fi.fileName()))
            continue;
        const QString to = dst + u'/' + fi.fileName();
        if (fi.isDir()) {
            if (!copyTree(fi.absoluteFilePath(), to, error))
                return false;
        } else if (fi.isFile()) {
            if (!QFile::copy(fi.absoluteFilePath(), to)) {
                *error = u"cannot copy %1"_s.arg(fi.fileName());
                return false;
            }
        }
    }
    return true;
}

// ---- icons

QString sniffImage(const QByteArray &d)
{
    if (d.startsWith("\x89PNG"))
        return u"png"_s;
    if (d.startsWith("\xFF\xD8\xFF"))
        return u"jpg"_s;
    if (d.startsWith("GIF8"))
        return u"gif"_s;
    if (d.size() > 12 && d.startsWith("RIFF") && d.mid(8, 4) == "WEBP")
        return u"webp"_s;
    if (d.startsWith(QByteArray("\x00\x00\x01\x00", 4)))
        return u"ico"_s;
    const QByteArray head = d.left(2048).toLower();
    if (head.contains("<svg"))
        return u"svg"_s;
    return {};
}

int maxSize(const QString &sizes)
{
    int best = 0;
    const auto parts = sizes.split(QRegularExpression(u"\\s+"_s), Qt::SkipEmptyParts);
    for (const QString &p : parts) {
        if (p.compare(u"any"_s, Qt::CaseInsensitive) == 0) {
            best = qMax(best, 1024);
            continue;
        }
        const auto xy = p.toLower().split(u'x');
        if (xy.size() == 2)
            best = qMax(best, qMax(xy[0].toInt(), xy[1].toInt()));
    }
    return best;
}

QHash<QString, QString> parseAttrs(const QString &tag)
{
    QHash<QString, QString> out;
    static const QRegularExpression re(u"([A-Za-z_:][-A-Za-z0-9_:.]*)\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s>\"']+))"_s);
    auto it = re.globalMatch(tag);
    while (it.hasNext()) {
        const auto m = it.next();
        QString v = m.captured(2);
        if (v.isNull())
            v = m.captured(3);
        if (v.isNull())
            v = m.captured(4);
        out.insert(m.captured(1).toLower(), v);
    }
    return out;
}

} // namespace

QList<IconCandidate> discoverIcons(const QUrl &page, const Fetcher &fetch, QString *title)
{
    QList<IconCandidate> out;
    if (!fetch)
        return out;
    const FetchResult pr = fetch(page);
    const QUrl base = pr.ok && pr.finalUrl.isValid() ? pr.finalUrl : page;
    QUrl manifestUrl;
    if (pr.ok) {
        const QString html = QString::fromUtf8(pr.data.left(512 * 1024));
        static const QRegularExpression titleRe(u"<title[^>]*>(.*?)</title>"_s,
                                                QRegularExpression::CaseInsensitiveOption | QRegularExpression::DotMatchesEverythingOption);
        const auto tm = titleRe.match(html);
        if (title && tm.hasMatch())
            *title = tm.captured(1).simplified();
        static const QRegularExpression linkRe(u"<link\\b[^>]*>"_s, QRegularExpression::CaseInsensitiveOption);
        auto it = linkRe.globalMatch(html);
        while (it.hasNext()) {
            const auto attrs = parseAttrs(it.next().captured(0));
            const QStringList rel = attrs.value(u"rel"_s).toLower().split(QRegularExpression(u"\\s+"_s), Qt::SkipEmptyParts);
            const QString href = attrs.value(u"href"_s).trimmed();
            if (href.isEmpty())
                continue;
            if (rel.contains(u"manifest"_s) && !manifestUrl.isValid()) {
                manifestUrl = base.resolved(QUrl(href));
            } else if (rel.contains(u"icon"_s) || rel.contains(u"apple-touch-icon"_s) || rel.contains(u"apple-touch-icon-precomposed"_s)) {
                IconCandidate c;
                c.url = base.resolved(QUrl(href));
                c.type = attrs.value(u"type"_s).toLower();
                c.size = maxSize(attrs.value(u"sizes"_s));
                const bool svg = c.type.contains(u"svg"_s) || href.toLower().endsWith(u".svg"_s);
                if (c.size == 0)
                    c.size = svg ? 1024 : (rel.contains(u"apple-touch-icon"_s) || rel.contains(u"apple-touch-icon-precomposed"_s) ? 180 : 32);
                out << c;
            }
        }
    }
    if (manifestUrl.isValid()) {
        const FetchResult mr = fetch(manifestUrl);
        if (mr.ok) {
            const QJsonObject m = QJsonDocument::fromJson(mr.data).object();
            if (title && title->isEmpty())
                *title = m.value(u"name"_s).toString(m.value(u"short_name"_s).toString());
            const QUrl mbase = mr.finalUrl.isValid() ? mr.finalUrl : manifestUrl;
            const QJsonArray icons = m.value(u"icons"_s).toArray();
            for (const QJsonValue &v : icons) {
                const QJsonObject io = v.toObject();
                if (io.value(u"purpose"_s).toString().contains(u"monochrome"_s))
                    continue;
                const QString src = io.value(u"src"_s).toString().trimmed();
                if (src.isEmpty())
                    continue;
                IconCandidate c;
                c.url = mbase.resolved(QUrl(src));
                c.type = io.value(u"type"_s).toString().toLower();
                c.size = maxSize(io.value(u"sizes"_s).toString());
                if (c.size == 0)
                    c.size = 64;
                out << c;
            }
        }
    }
    // The classic fallback.
    IconCandidate fav;
    fav.url = base.resolved(QUrl(u"/favicon.ico"_s));
    fav.size = 16;
    fav.type = u"image/x-icon"_s;
    out << fav;
    // Drop non-web schemes, de-duplicate, largest first (stable, so the page's own order breaks ties).
    QList<IconCandidate> clean;
    QSet<QString> seen;
    for (const IconCandidate &c : std::as_const(out)) {
        const QString s = c.url.scheme();
        if ((s != u"https"_s && s != u"http"_s) || seen.contains(c.url.toString()))
            continue;
        seen.insert(c.url.toString());
        clean << c;
    }
    std::stable_sort(clean.begin(), clean.end(), [](const IconCandidate &a, const IconCandidate &b) { return a.size > b.size; });
    return clean;
}

QString taosColorScheme()
{
    QProcess p;
    p.start(u"taos-theme"_s, {u"get"_s, u"--json"_s});
    if (!p.waitForFinished(3000)) {
        p.kill();
        p.waitForFinished(500);
        return {};
    }
    return QJsonDocument::fromJson(p.readAllStandardOutput()).object().value(u"variant"_s).toString();
}

bool portalInstalled()
{
    const QStringList dirs = {u"/usr/libexec"_s, u"/usr/lib/xdg-desktop-portal"_s, u"/usr/lib"_s, u"/usr/lib/x86_64-linux-gnu"_s,
                              u"/usr/lib/aarch64-linux-gnu"_s, u"/usr/local/libexec"_s};
    auto has = [&](const QString &name) {
        for (const QString &d : dirs)
            if (QFileInfo::exists(d + u'/' + name))
                return true;
        return false;
    };
    return has(u"xdg-desktop-portal"_s) && has(u"xdg-desktop-portal-gtk"_s);
}

Fetcher networkFetcher()
{
    return [](const QUrl &url) {
        FetchResult r;
        if (qEnvironmentVariableIsSet("TAOS_WEBAPP_NO_NETWORK")) {   // offline machines and tests: no attempt, a monogram instead
            r.error = u"network disabled"_s;
            return r;
        }
        if (url.scheme() != u"https"_s && url.scheme() != u"http"_s) {
            r.error = u"unsupported scheme"_s;
            return r;
        }
        QNetworkAccessManager nam;
        QNetworkRequest req(url);
        req.setTransferTimeout(8000);
        req.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::NoLessSafeRedirectPolicy);
        req.setHeader(QNetworkRequest::UserAgentHeader, u"taOS-webapp/1.0"_s);
        QNetworkReply *rep = nam.get(req);
        QEventLoop loop;
        QByteArray data;
        QObject::connect(rep, &QNetworkReply::readyRead, &loop, [&] {
            data += rep->readAll();
            if (data.size() > 2 * 1024 * 1024)
                rep->abort();
        });
        QObject::connect(rep, &QNetworkReply::finished, &loop, &QEventLoop::quit);
        QTimer::singleShot(10000, &loop, &QEventLoop::quit);
        loop.exec();
        data += rep->readAll();
        const bool finished = rep->isFinished();
        if (!finished)
            rep->abort();
        r.ok = finished && rep->error() == QNetworkReply::NoError && data.size() <= 2 * 1024 * 1024;
        r.error = rep->errorString();
        r.data = data;
        r.contentType = rep->header(QNetworkRequest::ContentTypeHeader).toString();
        r.finalUrl = rep->url();
        rep->deleteLater();
        return r;
    };
}

// ---------------------------------------------------------------- Engine

struct Engine::Private {
    ChromiumBackend chromium;
};

Engine::Engine(const Options &o) : d(new Private), m_o(o)
{
    if (!m_o.fetcher)
        m_o.fetcher = networkFetcher();
    if (!m_o.colorScheme)
        m_o.colorScheme = taosColorScheme;
    if (!m_o.portalPresent)
        m_o.portalPresent = portalInstalled;
    m_backend = m_o.backend ? m_o.backend : &d->chromium;
}

Engine::~Engine() { delete d; }

bool Engine::validId(const QString &id) { return idRe().match(id).hasMatch(); }

QString Engine::instanceDir(const QString &id) const
{
    if (!validId(id))
        return {};
    return m_o.root + u'/' + id;
}

QString Engine::profileDir(const QString &id) const
{
    const QString d0 = instanceDir(id);
    return d0.isEmpty() ? QString() : d0 + u"/profile"_s;
}

QString Engine::desktopFilePath(const QString &id) const
{
    return validId(id) ? m_o.applicationsDir + u"/taos-webapp-"_s + id + u".desktop"_s : QString();
}

QUrl Engine::normalizeUrl(const QString &input, bool allowHttp, QString *error)
{
    auto bad = [&](const QString &why) {
        if (error)
            *error = why;
        return QUrl();
    };
    QString s = input.trimmed();
    if (s.isEmpty())
        return bad(u"Enter the address of the website."_s);
    if (s.contains(QRegularExpression(u"\\s"_s)))
        return bad(u"An address has no spaces in it."_s);
    static const QRegularExpression bannedScheme(u"^(javascript|data|file|blob|about|chrome|view-source|mailto|tel|ftp|ws|wss|vbscript):"_s,
                                                 QRegularExpression::CaseInsensitiveOption);
    if (bannedScheme.match(s).hasMatch())
        return bad(u"Only web addresses (https) can be made into a web app."_s);
    if (!s.contains(u"://"_s))
        s = u"https://"_s + s;
    const QUrl u(s, QUrl::StrictMode);
    if (!u.isValid() || u.host().isEmpty())
        return bad(u"That is not a valid web address."_s);
    const QString scheme = u.scheme().toLower();
    if (scheme == u"http"_s) {
        if (!allowHttp)
            return bad(u"The address must start with https. Plain http is only allowed with --allow-http."_s);
    } else if (scheme != u"https"_s) {
        return bad(u"Only web addresses (https) can be made into a web app."_s);
    }
    if (!u.userInfo().isEmpty())
        return bad(u"An address with a user name or password in it is not accepted."_s);
    QUrl n = u;
    n.setScheme(scheme);
    n.setHost(u.host().toLower());
    return n;
}

QString Engine::hostLabel(const QUrl &url)
{
    QString h = url.host();
    if (h.startsWith(u"www."_s))
        h = h.mid(4);
    return h;
}

QString Engine::monogramSvg(const QString &name)
{
    QChar letter = u'?';
    for (QChar c : name) {
        if (c.isLetterOrNumber()) {
            letter = c.toUpper();
            break;
        }
    }
    static const char *palette[] = {"#4F6D8C", "#477F72", "#7E7240", "#8F5E4E", "#5E7650", "#4F7482", "#7B6A58", "#6C7078"};
    quint32 h = 2166136261u;
    for (QChar c : name.toLower()) {
        h ^= c.unicode();
        h *= 16777619u;
    }
    const QString fill = QString::fromLatin1(palette[h % 8]);
    return u"<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 128 128\" width=\"128\" height=\"128\">"
           "<rect width=\"128\" height=\"128\" rx=\"28\" fill=\"%1\"/>"
           "<text x=\"64\" y=\"88\" font-family=\"Inter, Helvetica, Arial, sans-serif\" font-size=\"68\" font-weight=\"600\" "
           "text-anchor=\"middle\" fill=\"#FFFFFF\">%2</text></svg>"_s.arg(fill, xmlEscape(QString(letter)));
}

// ---- index

QList<Instance> Engine::loadIndex() const
{
    QList<Instance> out;
    QFile f(m_o.root + u"/index.json"_s);
    if (!f.open(QIODevice::ReadOnly))
        return out;
    const QJsonArray arr = QJsonDocument::fromJson(f.readAll()).object().value(u"instances"_s).toArray();
    for (const QJsonValue &v : arr) {
        Instance i = Instance::fromJson(v.toObject());
        if (validId(i.id))
            out << i;
    }
    return out;
}

bool Engine::saveIndex(const QList<Instance> &all, QString *error) const
{
    QJsonArray arr;
    for (const Instance &i : all)
        arr.append(i.toJson());
    QJsonObject root;
    root[u"version"_s] = 1;
    root[u"instances"_s] = arr;
    QDir().mkpath(m_o.root);
    return writeFileAtomic(m_o.root + u"/index.json"_s, QJsonDocument(root).toJson(QJsonDocument::Indented), error);
}

QList<Instance> Engine::list() const
{
    return loadIndex();
}

Result Engine::ensureOwnTitleBar(const QString &id)
{
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    if (isRunning(id))
        return Result::fail(u"running"_s, u"%1 is open: its preferences are not touched while it runs."_s.arg(inst.name));
    const QString dir = profileDir(id) + u'/' + inst.profile;
    const QString path = dir + u"/Preferences"_s;
    QJsonObject root;
    QFile f(path);
    if (f.open(QIODevice::ReadOnly)) {
        QJsonParseError pe;
        const QJsonDocument d = QJsonDocument::fromJson(f.readAll(), &pe);
        f.close();
        if (pe.error != QJsonParseError::NoError || !d.isObject())
            return Result::fail(u"io"_s, u"The preferences of %1 could not be read, so they were left alone."_s.arg(inst.name));
        root = d.object();
    }
    QJsonObject browser = root.value(u"browser"_s).toObject();
    if (browser.value(u"custom_chrome_frame"_s).toBool(false))
        return Result::success();
    browser.insert(u"custom_chrome_frame"_s, true);
    root.insert(u"browser"_s, browser);
    if (!QDir().mkpath(dir))
        return Result::fail(u"io"_s, u"Could not create the profile folder."_s);
    QString err;
    if (!writeFileAtomic(path, QJsonDocument(root).toJson(QJsonDocument::Compact), &err))
        return Result::fail(u"io"_s, u"Could not write the preferences: %1"_s.arg(err));
    return Result::success();
}

void Engine::syncLaunchers() const
{
    for (const Instance &i : loadIndex()) {
        QFile f(desktopFilePath(i.id));
        const QByteArray want = desktopText(i).toUtf8();
        if (f.open(QIODevice::ReadOnly) && f.readAll() == want)
            continue;
        f.close();
        QDir().mkpath(m_o.applicationsDir);
        writeFileAtomic(desktopFilePath(i.id), want, nullptr);
    }
}

Result Engine::get(const QString &id, Instance *out) const
{
    if (!validId(id))
        return Result::fail(u"invalid_id"_s, u"That is not a web app id."_s);
    for (const Instance &i : loadIndex()) {
        if (i.id == id) {
            if (out)
                *out = i;
            return Result::success(i.toJson());
        }
    }
    return Result::fail(u"not_found"_s, u"There is no web app with the id %1."_s.arg(id));
}

namespace {
// Run `fn(list)` with the index locked; `fn` returns false to skip the write.
struct IndexLock {
    explicit IndexLock(const QString &root) : lock(root + u"/.index.lock"_s)
    {
        QDir().mkpath(root);
        lock.setStaleLockTime(30000);
        ok = lock.tryLock(8000);
    }
    QLockFile lock;
    bool ok = false;
};
} // namespace

// ---- files of one instance

Result Engine::writeManifest(const Instance &inst) const
{
    QJsonObject o = inst.toJson();
    o[u"schema"_s] = 1;
    QString err;
    if (!writeFileAtomic(instanceDir(inst.id) + u"/manifest.json"_s, QJsonDocument(o).toJson(QJsonDocument::Indented), &err))
        return Result::fail(u"io"_s, u"Could not write the web app's manifest: %1"_s.arg(err));
    return Result::success();
}

QString Engine::desktopText(const Instance &inst) const
{
    const QString exec = desktopExecQuote(m_o.execName);
    QString t;
    t += u"[Desktop Entry]\nType=Application\nVersion=1.5\n"_s;
    t += u"Name=%1\n"_s.arg(desktopEscape(inst.name));
    t += u"Comment=Web app: %1\n"_s.arg(desktopEscape(hostLabel(QUrl(inst.url))));
    t += u"Exec=%1 launch %2\n"_s.arg(exec, inst.id);
    if (!inst.icon.isEmpty())
        t += u"Icon=%1\n"_s.arg(desktopEscape(inst.icon));
    t += u"Terminal=false\nCategories=Network;\n"_s;
    t += u"StartupWMClass=%1\nStartupNotify=true\n"_s.arg(inst.appId());
    t += u"X-taOS-AppId=%1\nX-taOS-WebApp=%2\n"_s.arg(inst.appId(), inst.id);
    t += u"Actions=clone;clear-data;\n\n"_s;
    t += u"[Desktop Action clone]\nName=Clone (keeps the sign-in)\nExec=%1 clone %2\n\n"_s.arg(exec, inst.id);
    t += u"[Desktop Action clear-data]\nName=Clear data\nExec=%1 clear-data %2\n"_s.arg(exec, inst.id);
    return t;
}

Result Engine::writeDesktopFile(const Instance &inst) const
{
    QDir().mkpath(m_o.applicationsDir);
    QString err;
    if (!writeFileAtomic(desktopFilePath(inst.id), desktopText(inst).toUtf8(), &err))
        return Result::fail(u"io"_s, u"Could not write the launcher entry: %1"_s.arg(err));
    return Result::success();
}

Result Engine::writeIcon(const QString &id, const QString &spec, const QUrl &page, const QString &monoName, QString *iconPath, QString *titleOut)
{
    const QString dir = instanceDir(id);
    auto save = [&](const QByteArray &data, bool strict, QString *why) -> bool {
        const QString kind = sniffImage(data);
        if (kind.isEmpty()) {
            *why = u"The icon is not an image."_s;
            return false;
        }
        if (kind == u"svg"_s) {
            const QByteArray low = data.toLower();
            if (data.size() > 512 * 1024 || low.contains("<script") || low.contains("javascript:") || low.contains("<foreignobject")) {
                *why = u"The SVG icon is not accepted."_s;
                return false;
            }
            const QString p = dir + u"/icon.svg"_s;
            if (!writeFileAtomic(p, data, why))
                return false;
            QFile::remove(dir + u"/icon.png"_s);
            *iconPath = p;
            return true;
        }
        QImage img = QImage::fromData(data);
        if (img.isNull()) {
            *why = strict ? u"The icon image could not be read."_s : u"unreadable"_s;
            return false;
        }
        if (img.width() > 256 || img.height() > 256)
            img = img.scaled(256, 256, Qt::KeepAspectRatio, Qt::SmoothTransformation);
        const QString p = dir + u"/icon.png"_s;
        QSaveFile f(p);
        if (!f.open(QIODevice::WriteOnly) || !img.save(&f, "PNG") || !f.commit()) {
            *why = u"The icon could not be saved."_s;
            return false;
        }
        QFile::remove(dir + u"/icon.svg"_s);
        *iconPath = p;
        return true;
    };

    QString why;
    if (spec.isEmpty() || spec == u"auto"_s) {
        bool got = false;
        if (spec == u"auto"_s) {
            const QList<IconCandidate> cands = discoverIcons(page, m_o.fetcher, titleOut);
            int tries = 0;
            for (const IconCandidate &c : cands) {
                if (++tries > 5)
                    break;
                const FetchResult fr = m_o.fetcher(c.url);
                if (fr.ok && save(fr.data, false, &why)) {
                    got = true;
                    break;
                }
            }
        }
        if (!got) {
            // No network, no icon on the site: a monogram made here.
            const QString p = dir + u"/icon.svg"_s;
            QString err;
            if (!writeFileAtomic(p, monogramSvg(!monoName.isEmpty() ? monoName : (!titleOut->isEmpty() ? *titleOut : hostLabel(page))).toUtf8(), &err))
                return Result::fail(u"io"_s, u"Could not write the icon: %1"_s.arg(err));
            *iconPath = p;
            QJsonObject d;
            d[u"icon_fallback"_s] = true;
            return Result::success(d);
        }
        return Result::success();
    }
    QFile f(spec);
    if (!f.open(QIODevice::ReadOnly) || f.size() > 8 * 1024 * 1024)
        return Result::fail(u"invalid_icon"_s, u"The icon file %1 could not be read."_s.arg(spec));
    if (!save(f.readAll(), true, &why))
        return Result::fail(u"invalid_icon"_s, why);
    return Result::success();
}

// ---- running

QStringList Engine::readEnvFile(const QString &id) const
{
    QStringList out;
    QFile f(instanceDir(id) + u"/env"_s);
    if (!f.open(QIODevice::ReadOnly))
        return out;
    for (const QByteArray &raw : f.readAll().split('\n')) {
        const QString line = QString::fromUtf8(raw).trimmed();
        if (line.isEmpty() || line.startsWith(u'#'))
            continue;
        const int eq = line.indexOf(u'=');
        if (eq <= 0)
            continue;
        const QString key = line.left(eq).trimmed();
        if (!QRegularExpression(u"^[A-Za-z_][A-Za-z0-9_]*$"_s).match(key).hasMatch())
            continue;
        out << key + u'=' + line.mid(eq + 1).trimmed();
    }
    return out;
}

static qint64 ownerPid(const BrowserBackend *b, const QString &profileDir, const QString &instDir)
{
    if (const qint64 p = b->profileOwnerPid(profileDir))
        return p;
    QFile f(instDir + u"/launch.pid"_s);
    if (f.open(QIODevice::ReadOnly)) {
        const qint64 pid = f.readAll().trimmed().toLongLong();
        if (pidAlive(pid)) {
            QFile cmd(u"/proc/%1/cmdline"_s.arg(pid));
            if (cmd.open(QIODevice::ReadOnly) && QString::fromLocal8Bit(cmd.readAll()).contains(profileDir))
                return pid;
        }
    }
    return 0;
}

bool Engine::isRunning(const QString &id) const
{
    if (!validId(id))
        return false;
    return ownerPid(m_backend, profileDir(id), instanceDir(id)) != 0;
}

qint64 Engine::profileSize(const QString &id) const
{
    return validId(id) ? dirSize(instanceDir(id)) : 0;
}

// ---- commands

Result Engine::add(const AddRequest &req)
{
    QString err;
    const QUrl url = normalizeUrl(req.url, req.allowHttp, &err);
    if (!url.isValid())
        return Result::fail(u"invalid_url"_s, err);
    if (!req.icon.isEmpty() && req.icon != u"auto"_s && !QFileInfo(req.icon).isFile())
        return Result::fail(u"invalid_icon"_s, u"The icon file %1 does not exist."_s.arg(req.icon));

    IndexLock lk(m_o.root);
    if (!lk.ok)
        return Result::fail(u"io"_s, u"The web app registry is busy. Try again."_s);
    QList<Instance> all = loadIndex();
    QSet<QString> taken;
    for (const Instance &i : all)
        taken.insert(i.id);
    QString id;
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789";
    do {
        id.clear();
        for (int k = 0; k < 10; ++k)
            id += QLatin1Char(alphabet[QRandomGenerator::system()->bounded(36)]);
    } while (taken.contains(id) || QFileInfo::exists(instanceDir(id)));

    const QString dir = instanceDir(id);
    auto cleanup = [&] { QDir(dir).removeRecursively(); };
    if (!QDir().mkpath(dir + u"/profile"_s))
        return Result::fail(u"io"_s, u"Could not create the web app's folder."_s);
    QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    QFile::setPermissions(dir + u"/profile"_s, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);

    Instance inst;
    inst.id = id;
    inst.profile = id;   // its own profile directory: its own window id, even next to another instance of the same site
    inst.url = url.toString(QUrl::FullyEncoded);
    inst.source = req.source.isEmpty() ? u"custom"_s : req.source;
    inst.browser = req.browser.isEmpty() ? u"auto"_s : req.browser;
    inst.created = QDateTime::fromString(isoNow(m_o.clock), Qt::ISODate);

    QString name = cleanName(req.name);
    QString iconPath;
    QString discoveredTitle;
    Result ir = writeIcon(id, req.icon, url, name, &iconPath, &discoveredTitle);
    if (!ir.ok) {
        cleanup();
        return ir;
    }
    if (name.isEmpty())
        name = cleanName(discoveredTitle);
    if (name.isEmpty())
        name = hostLabel(url);
    inst.name = name;
    inst.icon = iconPath;

    Result mr = writeManifest(inst);
    if (!mr.ok) {
        cleanup();
        return mr;
    }
    Result dr = writeDesktopFile(inst);
    if (!dr.ok) {
        cleanup();
        return dr;
    }
    all << inst;
    if (!saveIndex(all, &err)) {
        cleanup();
        QFile::remove(desktopFilePath(id));
        return Result::fail(u"io"_s, u"Could not save the web app registry: %1"_s.arg(err));
    }
    ensureOwnTitleBar(id);   // before the first launch
    QJsonObject d = inst.toJson();
    if (ir.data.value(u"icon_fallback"_s).toBool())
        d[u"icon_fallback"_s] = true;
    return Result::success(d);
}

Result Engine::launch(const QString &id)
{
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    const QString program = m_backend->probe(inst.browser);
    if (program.isEmpty())
        return Result::fail(u"no_browser"_s, u"No browser engine installed. Web apps need Chromium (Debian: apt install chromium)."_s);
    // Two instances with the same window id (the same site, both in the Default profile) cannot be told apart by the
    // dock: the second does not start while the first is open.
    for (const Instance &other : list()) {
        if (other.id != id && other.appId() == inst.appId() && isRunning(other.id))
            return Result::fail(u"collision"_s,
                                u"%1 is open and has the same window id as %2 (the same site in the same profile). Use Clone to make a second copy with its own window id."_s
                                    .arg(other.name, inst.name));
    }
    syncLaunchers();
    ensureOwnTitleBar(id);   // repairs older instances; skipped (refused) while the window is already open
    const QString pdir = profileDir(id);
    QDir().mkpath(pdir);
    LaunchSpec spec = m_backend->launchSpec(program, inst, pdir);
    // Chromium takes its dark or light look from the desktop portal, live; with no portal it reads the GTK setting once at
    // start. So without a portal a dark taOS passes the explicit switch: dark browser UI and prefers-color-scheme: dark.
    // (Not WebContentsForceDark: that darkens page content itself.)
    if (!m_o.portalPresent() && m_o.colorScheme() == u"dark"_s)
        spec.arguments << u"--force-dark-mode"_s;
    QProcess p;
    p.setProgram(spec.program);
    p.setArguments(spec.arguments);
    // A foreign app does not get taOS's own Qt environment (the wrapper that started us sourced it for us): the library
    // path, Qt and QML variables go. The GPU session variables (EGL, Mesa) stay: the Intel session needs them.
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    for (const QString &k : env.keys())
        if (k == u"LD_LIBRARY_PATH"_s || k.startsWith(u"QT_"_s) || k.startsWith(u"QML"_s))
            env.remove(k);
    for (const QString &kv : readEnvFile(id)) {
        const int eq = kv.indexOf(u'=');
        env.insert(kv.left(eq), kv.mid(eq + 1));
    }
    p.setProcessEnvironment(env);
    p.setStandardInputFile(QProcess::nullDevice());
    p.setStandardOutputFile(QProcess::nullDevice());
    p.setStandardErrorFile(QProcess::nullDevice());
    qint64 pid = 0;
    if (!p.startDetached(&pid))
        return Result::fail(u"io"_s, u"The browser could not be started: %1"_s.arg(p.errorString()));
    writeFileAtomic(instanceDir(id) + u"/launch.pid"_s, QByteArray::number(pid), nullptr);
    {
        IndexLock lk(m_o.root);
        if (lk.ok) {
            QList<Instance> all = loadIndex();
            for (Instance &i : all)
                if (i.id == id)
                    i.lastUsed = QDateTime::fromString(isoNow(m_o.clock), Qt::ISODate);
            saveIndex(all, nullptr);
            for (const Instance &i : std::as_const(all))
                if (i.id == id)
                    writeManifest(i);
        }
    }
    QJsonObject d;
    d[u"pid"_s] = pid;
    d[u"program"_s] = spec.program;
    d[u"arguments"_s] = QJsonArray::fromStringList(spec.arguments);
    d[u"app_id"_s] = inst.appId();
    return Result::success(d);
}

Result Engine::clone(const QString &id, const QString &newName)
{
    Instance src;
    Result g = get(id, &src);
    if (!g.ok)
        return g;
    if (isRunning(id))
        return Result::fail(u"running"_s, u"%1 is open. Close its window first, so the sign-in is copied whole."_s.arg(src.name));
    IndexLock lk(m_o.root);
    if (!lk.ok)
        return Result::fail(u"io"_s, u"The web app registry is busy. Try again."_s);
    QList<Instance> all = loadIndex();
    QSet<QString> taken;
    for (const Instance &i : all)
        taken.insert(i.id);
    QString nid;
    static const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789";
    do {
        nid.clear();
        for (int k = 0; k < 10; ++k)
            nid += QLatin1Char(alphabet[QRandomGenerator::system()->bounded(36)]);
    } while (taken.contains(nid) || QFileInfo::exists(instanceDir(nid)));

    const QString dir = instanceDir(nid);
    const QString partial = dir + u".partial"_s;
    QDir(partial).removeRecursively();
    QString err;
    if (!QDir().mkpath(partial) || !copyTree(profileDir(id), partial + u"/profile"_s, &err)) {
        QDir(partial).removeRecursively();
        return Result::fail(u"io"_s, u"Could not copy the profile: %1"_s.arg(err));
    }
    // The copy gets its own profile directory name (= its id), so its window id differs from the original's. Chromium
    // knows profiles by directory name in `Local State`: that file is patched to match.
    if (QFileInfo(partial + u"/profile/"_s + src.profile).isDir() && src.profile != nid) {
        if (!QDir().rename(partial + u"/profile/"_s + src.profile, partial + u"/profile/"_s + nid)) {
            QDir(partial).removeRecursively();
            return Result::fail(u"io"_s, u"Could not rename the copied profile."_s);
        }
        patchLocalState(partial + u"/profile/Local State"_s, src.profile, nid);
    }
    QFile::setPermissions(partial, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    // the env file and the icon come along
    for (const QString &f : {u"env"_s}) {
        if (QFileInfo::exists(instanceDir(id) + u'/' + f))
            QFile::copy(instanceDir(id) + u'/' + f, partial + u'/' + f);
    }
    QString iconName;
    if (!src.icon.isEmpty() && QFileInfo::exists(src.icon)) {
        iconName = QFileInfo(src.icon).fileName();
        QFile::copy(src.icon, partial + u'/' + iconName);
    }
    if (!QDir().rename(partial, dir)) {
        QDir(partial).removeRecursively();
        return Result::fail(u"io"_s, u"Could not finish the copy."_s);
    }
    Instance c = src;
    c.id = nid;
    c.profile = nid;
    c.name = cleanName(newName).isEmpty() ? src.name + u" copy"_s : cleanName(newName);
    c.icon = iconName.isEmpty() ? QString() : dir + u'/' + iconName;
    c.created = QDateTime::fromString(isoNow(m_o.clock), Qt::ISODate);
    c.lastUsed = QDateTime();
    Result mr = writeManifest(c);
    Result dr = writeDesktopFile(c);
    all << c;
    if (!mr.ok || !dr.ok || !saveIndex(all, &err)) {
        QDir(dir).removeRecursively();
        QFile::remove(desktopFilePath(nid));
        return Result::fail(u"io"_s, mr.ok ? (dr.ok ? u"Could not save the web app registry: %1"_s.arg(err) : dr.error) : mr.error);
    }
    ensureOwnTitleBar(nid);
    return Result::success(c.toJson());
}

Result Engine::rename(const QString &id, const QString &name)
{
    const QString n = cleanName(name);
    if (n.isEmpty())
        return Result::fail(u"invalid"_s, u"The name cannot be empty."_s);
    IndexLock lk(m_o.root);
    if (!lk.ok)
        return Result::fail(u"io"_s, u"The web app registry is busy. Try again."_s);
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    QList<Instance> all = loadIndex();
    for (Instance &i : all)
        if (i.id == id) {
            i.name = n;
            inst = i;
        }
    QString err;
    if (!saveIndex(all, &err))
        return Result::fail(u"io"_s, u"Could not save the web app registry: %1"_s.arg(err));
    writeManifest(inst);
    Result dr = writeDesktopFile(inst);
    if (!dr.ok)
        return dr;
    return Result::success(inst.toJson());
}

static bool terminate(qint64 pid)
{
    ::kill(static_cast<pid_t>(pid), SIGTERM);
    for (int i = 0; i < 40; ++i) {
        if (!pidAlive(pid))
            return true;
        QThread::msleep(100);
    }
    return !pidAlive(pid);
}

Result Engine::remove(const QString &id, bool keepData, bool force)
{
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    const qint64 pid = ownerPid(m_backend, profileDir(id), instanceDir(id));
    if (pid) {
        if (!force)
            return Result::fail(u"running"_s, u"%1 is open. Close its window first, or remove it with --force."_s.arg(inst.name));
        if (!terminate(pid))
            return Result::fail(u"running"_s, u"%1 would not close."_s.arg(inst.name));
    }
    IndexLock lk(m_o.root);
    if (!lk.ok)
        return Result::fail(u"io"_s, u"The web app registry is busy. Try again."_s);
    QList<Instance> all = loadIndex();
    QList<Instance> rest;
    for (const Instance &i : all)
        if (i.id != id)
            rest << i;
    QString err;
    if (!saveIndex(rest, &err))
        return Result::fail(u"io"_s, u"Could not save the web app registry: %1"_s.arg(err));
    QFile::remove(desktopFilePath(id));
    if (!keepData) {
        // QDir::removeRecursively removes a symlink itself and never follows it.
        const QFileInfo fi(instanceDir(id));
        if (fi.isSymLink())
            QFile::remove(fi.filePath());
        else
            QDir(instanceDir(id)).removeRecursively();
    }
    QJsonObject d;
    d[u"id"_s] = id;
    d[u"kept_data"_s] = keepData;
    return Result::success(d);
}

Result Engine::clearData(const QString &id, bool force)
{
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    const qint64 pid = ownerPid(m_backend, profileDir(id), instanceDir(id));
    if (pid) {
        if (!force)
            return Result::fail(u"running"_s, u"%1 is open. Close its window first, or clear with --force."_s.arg(inst.name));
        if (!terminate(pid))
            return Result::fail(u"running"_s, u"%1 would not close."_s.arg(inst.name));
    }
    const QString p = profileDir(id);
    const QFileInfo fi(p);
    if (fi.isSymLink())
        QFile::remove(p);
    else
        QDir(p).removeRecursively();
    if (!QDir().mkpath(p))
        return Result::fail(u"io"_s, u"Could not recreate the profile folder."_s);
    QFile::setPermissions(p, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    return Result::success(QJsonObject{{u"id"_s, id}});
}

Result Engine::info(const QString &id) const
{
    Instance inst;
    Result g = get(id, &inst);
    if (!g.ok)
        return g;
    QJsonObject o = inst.toJson();
    o[u"profile_dir"_s] = profileDir(id);
    o[u"size_bytes"_s] = profileSize(id);
    o[u"running"_s] = isRunning(id);
    o[u"desktop_file"_s] = desktopFilePath(id);
    QJsonValue perms = QJsonValue::Null;
    QFile pf(profileDir(id) + u'/' + inst.profile + u"/Preferences"_s);
    if (pf.open(QIODevice::ReadOnly)) {
        const QJsonObject prefs = QJsonDocument::fromJson(pf.readAll()).object();
        const QJsonObject ex = prefs.value(u"profile"_s).toObject().value(u"content_settings"_s).toObject().value(u"exceptions"_s).toObject();
        if (!ex.isEmpty() || prefs.contains(u"profile"_s)) {
            QJsonObject out;
            for (const QString &k : {u"notifications"_s, u"geolocation"_s, u"media_stream_camera"_s, u"media_stream_mic"_s, u"clipboard"_s}) {
                QJsonArray allowed, blocked;
                const QJsonObject e = ex.value(k).toObject();
                for (auto it = e.begin(); it != e.end(); ++it) {
                    const int s = it.value().toObject().value(u"setting"_s).toInt();
                    if (s == 1)
                        allowed.append(it.key());
                    else if (s == 2)
                        blocked.append(it.key());
                }
                if (!allowed.isEmpty() || !blocked.isEmpty())
                    out[k] = QJsonObject{{u"allowed"_s, allowed}, {u"blocked"_s, blocked}};
            }
            perms = out;
        }
    }
    o[u"permissions"_s] = perms;
    return Result::success(o);
}

Result Engine::doctor(bool withVersions) const
{
    QJsonObject d;
    QJsonArray found;
    QString chosen;
    const QString pinned = qEnvironmentVariable("TAOS_WEBAPP_BROWSER");
    QStringList names = pinned.isEmpty() ? m_backend->candidates() : QStringList{pinned};
    for (const QString &n : std::as_const(names)) {
        QString path = n.contains(u'/') ? (QFileInfo(n).isExecutable() ? n : QString()) : QStandardPaths::findExecutable(n);
        if (path.isEmpty())
            continue;
        QJsonObject b;
        b[u"name"_s] = n;
        b[u"path"_s] = path;
        if (withVersions) {
            QProcess p;
            p.start(path, {u"--version"_s});
            if (p.waitForFinished(3000))
                b[u"version"_s] = QString::fromUtf8(p.readAllStandardOutput()).simplified();
            else {
                p.kill();
                p.waitForFinished(500);
            }
        }
        found.append(b);
    }
    chosen = m_backend->probe(u"auto"_s);
    d[u"backend"_s] = m_backend->name();
    d[u"browsers"_s] = found;
    d[u"chosen"_s] = chosen;
    d[u"root"_s] = m_o.root;
    d[u"applications_dir"_s] = m_o.applicationsDir;
    d[u"wayland"_s] = qEnvironmentVariableIsSet("WAYLAND_DISPLAY");
    QJsonArray orphans;
    QSet<QString> known;
    for (const Instance &i : loadIndex())
        known.insert(i.id);
    for (const QString &e : QDir(m_o.root).entryList(QDir::Dirs | QDir::NoDotAndDotDot))
        if (validId(e) && !known.contains(e))
            orphans.append(e);
    d[u"orphan_data"_s] = orphans;
    if (chosen.isEmpty()) {
        Result r = Result::fail(u"no_browser"_s,
                                u"No browser engine installed. Web apps need Chromium (Debian: apt install chromium). Looked for: %1."_s
                                    .arg(names.join(u", "_s)));
        r.data = d;
        return r;
    }
    return Result::success(d);
}

} // namespace TaosWebapp
