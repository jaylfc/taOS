// SPDX-License-Identifier: AGPL-3.0-or-later
#include "desktopentry.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QHash>
#include <QSet>
#include <QStandardPaths>

namespace taos::search {

static QString unescapeValue(const QString &v) {
    QString o;
    o.reserve(v.size());
    for (int i = 0; i < v.size(); ++i) {
        if (v[i] == QLatin1Char('\\') && i + 1 < v.size()) {
            const QChar n = v[++i];
            if (n == QLatin1Char('s')) o += QLatin1Char(' ');
            else if (n == QLatin1Char('n')) o += QLatin1Char('\n');
            else if (n == QLatin1Char('t')) o += QLatin1Char('\t');
            else if (n == QLatin1Char('r')) o += QLatin1Char('\r');
            else o += n;   // \\ and \; and anything else: the character itself
        } else o += v[i];
    }
    return o;
}

// Split a list value on unescaped ';'.
static QStringList splitList(const QString &v) {
    QStringList out;
    QString cur;
    for (int i = 0; i < v.size(); ++i) {
        if (v[i] == QLatin1Char('\\') && i + 1 < v.size()) {
            if (v[i + 1] == QLatin1Char(';')) { cur += QLatin1Char(';'); ++i; continue; }
            cur += v[i]; cur += v[++i];
        } else if (v[i] == QLatin1Char(';')) {
            if (!cur.isEmpty()) out << unescapeValue(cur);
            cur.clear();
        } else cur += v[i];
    }
    if (!cur.isEmpty()) out << unescapeValue(cur);
    return out;
}

bool DesktopEntry::showable(const QStringList &desktops) const {
    if (!valid() || noDisplay || hidden) return false;
    if (!desktops.isEmpty()) {
        if (!onlyShowIn.isEmpty()) {
            bool any = false;
            for (const QString &d : desktops) if (onlyShowIn.contains(d, Qt::CaseInsensitive)) any = true;
            if (!any) return false;
        }
        for (const QString &d : desktops) if (notShowIn.contains(d, Qt::CaseInsensitive)) return false;
    }
    return true;
}

DesktopEntry parseDesktopEntry(const QString &text, const QString &id, const QString &path, const QString &locale) {
    DesktopEntry e;
    e.id = id;
    e.path = path;
    // locale candidates, most specific first: lang_COUNTRY@MOD, lang_COUNTRY, lang@MOD, lang
    QString l = locale;
    l = l.section(QLatin1Char('.'), 0, 0);
    QString mod;
    if (l.contains(QLatin1Char('@'))) { mod = l.section(QLatin1Char('@'), 1); l = l.section(QLatin1Char('@'), 0, 0); }
    const QString lang = l.section(QLatin1Char('_'), 0, 0);
    const bool hasCountry = l.contains(QLatin1Char('_'));
    QStringList cands;
    if (hasCountry && !mod.isEmpty()) cands << l + QLatin1Char('@') + mod;
    if (hasCountry) cands << l;
    if (!mod.isEmpty() && !lang.isEmpty()) cands << lang + QLatin1Char('@') + mod;
    if (!lang.isEmpty() && lang != QLatin1String("C") && lang != QLatin1String("POSIX")) cands << lang;

    struct Loc { QString val; int rank = 1000; };
    QHash<QString, Loc> loc;   // key -> best localised value
    const auto putLoc = [&](const QString &key, const QString &tag, const QString &val) {
        const int r = tag.isEmpty() ? 999 : cands.indexOf(tag);
        if (r < 0) return;
        Loc &s = loc[key];
        if (r < s.rank) { s.rank = r; s.val = val; }
    };

    QString group;
    DesktopAction *curAction = nullptr;
    QHash<QString, DesktopAction> actionsById;
    QStringList actionOrder;
    const QStringList lines = text.split(QLatin1Char('\n'));
    // two-level: group-local locale maps are keyed "<group>/<key>"
    for (const QString &raw : lines) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith(QLatin1Char('#'))) continue;
        if (line.startsWith(QLatin1Char('['))) {
            if (!line.endsWith(QLatin1Char(']'))) { group = QStringLiteral("?"); continue; }
            group = line.mid(1, line.size() - 2);
            curAction = nullptr;
            if (group.startsWith(QLatin1String("Desktop Action "))) {
                const QString aid = group.mid(15);
                actionsById[aid].id = aid;
                curAction = &actionsById[aid];
                if (!actionOrder.contains(aid)) actionOrder << aid;
            }
            continue;
        }
        const int eq = line.indexOf(QLatin1Char('='));
        if (eq <= 0) continue;
        QString key = line.left(eq).trimmed();
        const QString val = line.mid(eq + 1).trimmed();
        QString tag;
        const int br = key.indexOf(QLatin1Char('['));
        if (br > 0 && key.endsWith(QLatin1Char(']'))) { tag = key.mid(br + 1, key.size() - br - 2); key = key.left(br); }
        if (group == QLatin1String("Desktop Entry")) {
            if (!tag.isEmpty()) { putLoc(key, tag, key == QLatin1String("Keywords") ? val : unescapeValue(val)); continue; }
            if (key == QLatin1String("Type")) e.isApplication = (val == QLatin1String("Application"));
            else if (key == QLatin1String("Name")) putLoc(key, QString(), unescapeValue(val));
            else if (key == QLatin1String("GenericName")) putLoc(key, QString(), unescapeValue(val));
            else if (key == QLatin1String("Comment")) putLoc(key, QString(), unescapeValue(val));
            else if (key == QLatin1String("Keywords")) putLoc(key, QString(), val);
            else if (key == QLatin1String("Icon")) e.icon = unescapeValue(val);
            else if (key == QLatin1String("Exec")) e.exec = val;
            else if (key == QLatin1String("TryExec")) e.tryExec = unescapeValue(val);
            else if (key == QLatin1String("Path")) e.path_ = unescapeValue(val);
            else if (key == QLatin1String("Terminal")) e.terminal = (val == QLatin1String("true"));
            else if (key == QLatin1String("NoDisplay")) e.noDisplay = (val == QLatin1String("true"));
            else if (key == QLatin1String("Hidden")) e.hidden = (val == QLatin1String("true"));
            else if (key == QLatin1String("Categories")) e.categories = splitList(val);
            else if (key == QLatin1String("OnlyShowIn")) e.onlyShowIn = splitList(val);
            else if (key == QLatin1String("NotShowIn")) e.notShowIn = splitList(val);
            else if (key == QLatin1String("X-taOS-AppId")) e.taosAppId = unescapeValue(val);
            else if (key == QLatin1String("X-taOS-AppGroup")) e.appGroup = unescapeValue(val);
            else if (key == QLatin1String("X-taOS-ContainerApp")) e.containerApp = unescapeValue(val);
            else if (key == QLatin1String("X-taOS-WebApp")) e.webApp = unescapeValue(val);
            else if (key == QLatin1String("Actions")) actionOrder = splitList(val) + actionOrder;
        } else if (curAction) {
            if (key == QLatin1String("Name")) {
                const QString k = QStringLiteral("A:%1:Name").arg(curAction->id);
                putLoc(k, tag, unescapeValue(val));
            } else if (key == QLatin1String("Exec") && tag.isEmpty()) curAction->exec = val;
        }
    }
    e.name = loc.value(QStringLiteral("Name")).val;
    e.genericName = loc.value(QStringLiteral("GenericName")).val;
    e.comment = loc.value(QStringLiteral("Comment")).val;
    // localised keywords: Keywords[de] lines were routed through putLoc with key "Keywords"
    // only when tagged; the plain list is the fallback. (Both land in the same slot, best rank wins.)
    {
        const Loc k = loc.value(QStringLiteral("Keywords"));
        // a localised Keywords value was stored raw; split it like the plain one
        e.keywords = splitList(k.val);
    }
    QSet<QString> seen;
    for (const QString &aid : std::as_const(actionOrder)) {
        if (seen.contains(aid) || !actionsById.contains(aid)) continue;
        seen.insert(aid);
        DesktopAction a = actionsById.value(aid);
        a.name = loc.value(QStringLiteral("A:%1:Name").arg(aid)).val;
        if (!a.name.isEmpty() && !a.exec.isEmpty()) e.actions << a;
    }
    return e;
}

QStringList execToArgv(const QString &exec, const DesktopEntry &e) {
    QStringList argv;
    QString cur;
    bool inQuote = false, have = false;
    const auto push = [&] { if (have) argv << cur; cur.clear(); have = false; };
    for (int i = 0; i < exec.size(); ++i) {
        const QChar c = exec[i];
        if (inQuote) {
            if (c == QLatin1Char('\\') && i + 1 < exec.size()) { cur += exec[++i]; have = true; }
            else if (c == QLatin1Char('"')) inQuote = false;
            else { cur += c; have = true; }
        } else if (c == QLatin1Char('"')) { inQuote = true; have = true; }
        else if (c == QLatin1Char(' ') || c == QLatin1Char('\t')) push();
        else { cur += c; have = true; }
    }
    push();
    QStringList out;
    for (const QString &a : std::as_const(argv)) {
        QString o;
        bool dropped = false;
        for (int i = 0; i < a.size(); ++i) {
            if (a[i] != QLatin1Char('%') || i + 1 >= a.size()) { o += a[i]; continue; }
            const QChar n = a[++i];
            if (n == QLatin1Char('%')) o += QLatin1Char('%');
            else if (n == QLatin1Char('c')) o += e.name;
            else if (n == QLatin1Char('k')) o += e.path;
            else if (n == QLatin1Char('i')) {
                if (a.size() == 2) { if (!e.icon.isEmpty()) { out << QStringLiteral("--icon") << e.icon; } dropped = true; }
            }
            // %f %F %u %U %d %D %n %N %v %m: no files or URLs are passed, drop the code
        }
        if (dropped) continue;
        if (o.isEmpty() && a.size() == 2 && a[0] == QLatin1Char('%')) continue;   // a bare field code
        if (o.isEmpty() && a.contains(QLatin1Char('%'))) continue;
        out << o;
    }
    return out;
}

QString desktopIdFor(const QString &root, const QString &file) {
    QString rel = QDir(root).relativeFilePath(file);
    rel.replace(QLatin1Char('/'), QLatin1Char('-'));
    return rel;
}

QStringList applicationDirs() {
    QStringList dirs;
    for (const QString &d : QStandardPaths::standardLocations(QStandardPaths::ApplicationsLocation))
        if (!dirs.contains(d)) dirs << d;
    for (const QString &d : { QStringLiteral("/usr/share/applications"), QStringLiteral("/usr/local/share/applications") })
        if (!dirs.contains(d)) dirs << d;
    return dirs;
}

QList<DesktopEntry> scanDesktopEntries(const QStringList &dirs, const QString &locale, const QStringList &desktops) {
    QList<DesktopEntry> out;
    QSet<QString> seen;
    for (const QString &dir : dirs) {
        if (!QFileInfo(dir).isDir()) continue;
        QDirIterator it(dir, { QStringLiteral("*.desktop") }, QDir::Files, QDirIterator::Subdirectories | QDirIterator::FollowSymlinks);
        while (it.hasNext()) {
            const QString f = it.next();
            const QString id = desktopIdFor(dir, f);
            if (seen.contains(id)) continue;   // an earlier directory owns this id, even if it hides it
            seen.insert(id);
            QFile fh(f);
            if (!fh.open(QIODevice::ReadOnly)) continue;
            const DesktopEntry e = parseDesktopEntry(QString::fromUtf8(fh.read(256 * 1024)), id, f, locale);
            if (!e.showable(desktops)) continue;
            if (!e.tryExec.isEmpty()) {
                const bool ok = e.tryExec.startsWith(QLatin1Char('/')) ? QFileInfo(e.tryExec).isExecutable()
                                                                       : !QStandardPaths::findExecutable(e.tryExec).isEmpty();
                if (!ok) continue;
            }
            out << e;
        }
    }
    return out;
}

}  // namespace taos::search
