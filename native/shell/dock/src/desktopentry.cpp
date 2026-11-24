#include "desktopentry.h"

#include <QDir>
#include <QDirIterator>
#include <QFile>
#include <QFileInfo>
#include <QLocale>
#include <QProcess>
#include <QStandardPaths>

namespace {
QString unescape(const QString &v) {
    QString o;
    for (int i = 0; i < v.size(); ++i) {
        if (v[i] == QLatin1Char('\\') && i + 1 < v.size()) {
            const QChar c = v[++i];
            if (c == QLatin1Char('s')) o += QLatin1Char(' ');
            else if (c == QLatin1Char('n')) o += QLatin1Char('\n');
            else if (c == QLatin1Char('t')) o += QLatin1Char('\t');
            else if (c == QLatin1Char('r')) o += QLatin1Char('\r');
            else o += c;   // \\ and anything else
        } else o += v[i];
    }
    return o;
}
QString norm(const QString &s) {
    QString o;
    for (QChar c : s) if (c.isLetterOrNumber()) o += c.toLower();
    return o;
}
}  // namespace

const DesktopAction *DesktopEntry::newWindowAction() const {
    for (const DesktopAction &a : actions)
        if (norm(a.id) == QLatin1String("newwindow")) return &a;
    for (const DesktopAction &a : actions)
        if (a.name.compare(QLatin1String("New Window"), Qt::CaseInsensitive) == 0) return &a;
    return nullptr;
}

QString DesktopEntry::execBase() const {
    QString prog;
    QStringList args;
    if (!Desktop::commandLine(exec, &prog, &args)) return {};
    if (QFileInfo(prog).fileName() == QLatin1String("env")) {
        for (const QString &a : args)
            if (!a.contains(QLatin1Char('=')) && !a.startsWith(QLatin1Char('-'))) { prog = a; break; }
    }
    return QFileInfo(prog).fileName();
}

namespace Desktop {

bool parse(const QByteArray &data, const QString &id, DesktopEntry *out) {
    DesktopEntry e;
    e.id = id;
    QString section;
    QString type;
    const QString lang = QLocale().name().section(QLatin1Char('_'), 0, 0);
    QString localisedName;
    QHash<QString, DesktopAction> actions;
    QStringList actionOrder;
    for (const QString &raw : QString::fromUtf8(data).split(QLatin1Char('\n'))) {
        const QString line = raw.trimmed();
        if (line.isEmpty() || line.startsWith(QLatin1Char('#'))) continue;
        if (line.startsWith(QLatin1Char('['))) { section = line.mid(1, line.size() - 2); continue; }
        const int eq = line.indexOf(QLatin1Char('='));
        if (eq <= 0) continue;
        const QString key = line.left(eq).trimmed();
        const QString val = unescape(line.mid(eq + 1).trimmed());
        if (section == QLatin1String("Desktop Entry")) {
            if (key == QLatin1String("Type")) type = val;
            else if (key == QLatin1String("Name")) e.name = val;
            else if (key == QLatin1String("Name[") + lang + QLatin1Char(']')) localisedName = val;
            else if (key == QLatin1String("Icon")) e.icon = val;
            else if (key == QLatin1String("Exec")) e.exec = val;
            else if (key == QLatin1String("StartupWMClass")) e.startupWMClass = val;
            else if (key == QLatin1String("X-taOS-AppId")) e.taosAppId = val;
            else if (key == QLatin1String("NoDisplay")) e.noDisplay = val == QLatin1String("true");
            else if (key == QLatin1String("Hidden")) e.hidden = val == QLatin1String("true");
            else if (key == QLatin1String("Terminal")) e.terminal = val == QLatin1String("true");
            else if (key == QLatin1String("SingleMainWindow")) e.singleMainWindow = val == QLatin1String("true");
            else if (key == QLatin1String("Actions")) actionOrder = val.split(QLatin1Char(';'), Qt::SkipEmptyParts);
        } else if (section.startsWith(QLatin1String("Desktop Action "))) {
            const QString aid = section.mid(15);
            DesktopAction &a = actions[aid];
            a.id = aid;
            if (key == QLatin1String("Name")) a.name = val;
            else if (key == QLatin1String("Exec")) a.exec = val;
        }
    }
    if (type != QLatin1String("Application")) return false;
    if (!localisedName.isEmpty()) e.name = localisedName;
    for (const QString &aid : actionOrder)
        if (actions.contains(aid) && !actions[aid].exec.isEmpty()) e.actions.append(actions[aid]);
    if (!e.valid() || e.hidden) return false;
    *out = e;
    return true;
}

bool commandLine(const QString &exec, QString *program, QStringList *args) {
    QString cleaned;
    for (int i = 0; i < exec.size(); ++i) {
        if (exec[i] == QLatin1Char('%') && i + 1 < exec.size()) {
            const QChar c = exec[++i];
            if (c == QLatin1Char('%')) cleaned += QLatin1Char('%');
            // %f %F %u %U %d %D %n %N %i %c %k %v %m: dropped (no files or URLs are passed from the dock)
            continue;
        }
        cleaned += exec[i];
    }
    QStringList parts = QProcess::splitCommand(cleaned);
    if (parts.isEmpty()) return false;
    *program = parts.takeFirst();
    *args = parts;
    return true;
}

QStringList defaultDirs() {
    QStringList dirs;
    const QString home = qEnvironmentVariable("XDG_DATA_HOME");
    dirs << (home.isEmpty() ? QDir::homePath() + QStringLiteral("/.local/share") : home) + QStringLiteral("/applications");
    QString sys = qEnvironmentVariable("XDG_DATA_DIRS");
    if (sys.isEmpty()) sys = QStringLiteral("/usr/local/share:/usr/share");
    for (const QString &d : sys.split(QLatin1Char(':'), Qt::SkipEmptyParts)) dirs << d + QStringLiteral("/applications");
    return dirs;
}

}  // namespace Desktop

void DesktopIndex::scan(const QStringList &dirs) {
    m_entries.clear();
    m_byId.clear();
    for (const QString &d : dirs) {
        QDirIterator it(d, {QStringLiteral("*.desktop")}, QDir::Files, QDirIterator::Subdirectories);
        QVector<QString> files;
        while (it.hasNext()) files << it.next();
        std::sort(files.begin(), files.end());
        for (const QString &f : files) {
            QString rel = QDir(d).relativeFilePath(f);
            rel.chop(8);
            rel.replace(QLatin1Char('/'), QLatin1Char('-'));
            if (m_byId.contains(rel)) continue;
            QFile file(f);
            if (!file.open(QIODevice::ReadOnly)) continue;
            DesktopEntry e;
            if (!Desktop::parse(file.readAll(), rel, &e)) continue;
            e.path = f;
            add(e);
        }
    }
}

void DesktopIndex::add(const DesktopEntry &e) {
    if (m_byId.contains(e.id)) return;
    m_byId.insert(e.id, m_entries.size());
    m_entries.append(e);
}

const DesktopEntry *DesktopIndex::byId(const QString &id) const {
    const auto it = m_byId.constFind(id);
    return it == m_byId.constEnd() ? nullptr : &m_entries[it.value()];
}

const DesktopEntry *DesktopIndex::matchWindow(const QString &appId) const {
    if (appId.isEmpty()) return nullptr;
    if (const DesktopEntry *e = byId(appId)) return e;
    const QString lower = appId.toLower();
    for (const DesktopEntry &e : m_entries)
        if (e.id.toLower() == lower) return &e;
    for (const DesktopEntry &e : m_entries)
        if (!e.startupWMClass.isEmpty() && e.startupWMClass.toLower() == lower) return &e;
    for (const DesktopEntry &e : m_entries)
        if (!e.taosAppId.isEmpty() && e.taosAppId.toLower() == lower) return &e;
    // org.gnome.Nautilus: the window says "nautilus" or the file is org.gnome.Nautilus
    for (const DesktopEntry &e : m_entries) {
        const QString last = e.id.section(QLatin1Char('.'), -1).toLower();
        if (e.id.contains(QLatin1Char('.')) && last == lower) return &e;
    }
    for (const DesktopEntry &e : m_entries)
        if (e.execBase().toLower() == lower) return &e;
    return nullptr;
}
