#include "appinfo.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>
#include <QTextStream>

AppInfo::AppInfo() {}

QStringList AppInfo::defaultDirs() {
    QStringList dirs;
    for (const QString &d : QStandardPaths::standardLocations(QStandardPaths::ApplicationsLocation)) dirs << d;
    for (const QString &d : { QStringLiteral("/usr/share/applications"), QStringLiteral("/usr/local/share/applications") })
        if (!dirs.contains(d)) dirs << d;
    return dirs;
}

static void indexFile(const QString &path, QHash<QString, AppInfo::Entry> *idx) {
    QFile f(path);
    if (!f.open(QIODevice::ReadOnly)) return;
    AppInfo::Entry e;
    QString wm;
    bool inEntry = false, hidden = false;
    QTextStream ts(&f);
    while (!ts.atEnd()) {
        const QString line = ts.readLine();
        if (line.startsWith(QLatin1Char('['))) { inEntry = line.trimmed() == QLatin1String("[Desktop Entry]"); continue; }
        if (!inEntry) continue;
        if (line.startsWith(QLatin1String("Name="))) e.name = line.mid(5).trimmed();
        else if (line.startsWith(QLatin1String("Icon="))) e.icon = line.mid(5).trimmed();
        else if (line.startsWith(QLatin1String("StartupWMClass="))) wm = line.mid(15).trimmed();
        else if (line.startsWith(QLatin1String("NoDisplay=true"))) hidden = true;
    }
    (void)hidden;
    const QString base = QFileInfo(path).completeBaseName();
    const auto put = [&](const QString &key, bool overwrite) {
        const QString k = key.toLower();
        if (k.isEmpty()) return;
        if (overwrite || !idx->contains(k)) idx->insert(k, e);
    };
    put(base, false);
    put(base.section(QLatin1Char('.'), -1), false);   // org.gnome.Foo -> foo
    put(wm, true);
}

QHash<QString, AppInfo::Entry> AppInfo::scan(const QStringList &dirs) {
    QHash<QString, Entry> idx;
    // earlier directories win: scan in reverse so they overwrite
    for (int i = dirs.size() - 1; i >= 0; --i) {
        QDir d(dirs[i]);
        QDirIterator it(d.absolutePath(), { QStringLiteral("*.desktop") }, QDir::Files, QDirIterator::Subdirectories);
        while (it.hasNext()) indexFile(it.next(), &idx);
    }
    return idx;
}

void AppInfo::startScan(const QStringList &dirs) {
    m_future = std::async(std::launch::async, [dirs] { return AppInfo::scan(dirs); }).share();
}

void AppInfo::setIndex(QHash<QString, Entry> idx) {
    m_cache = std::move(idx);
    m_ready = true;
}

void AppInfo::ensure() const {
    if (m_ready) return;
    if (m_future.valid()) m_cache = m_future.get();
    m_ready = true;
}

AppInfo::Entry AppInfo::lookup(const QString &appId) const {
    ensure();
    return m_cache.value(appId.toLower());
}

QString AppInfo::displayName(const QString &appId) const {
    const Entry e = lookup(appId);
    if (!e.name.isEmpty()) return e.name;
    if (appId.isEmpty()) return QStringLiteral("Window");
    QString n = appId.section(QLatin1Char('.'), -1);
    n[0] = n[0].toUpper();
    return n;
}
