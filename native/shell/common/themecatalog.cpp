#include "themecatalog.h"

#include <QDir>
#include <QFileInfo>
#include <algorithm>

// The shipped themes, in the order the picker shows them.
static const char *const kShippedOrder[] = {"graphite", "graphite-ice", "graphite-warm"};

static void initThemeResources() { Q_INIT_RESOURCE(taos_themes); }

QString ThemeCatalog::userDir() {
    const QString xdg = qEnvironmentVariable("XDG_DATA_HOME");
    return (xdg.isEmpty() ? QDir::homePath() + QStringLiteral("/.local/share") : xdg) + QStringLiteral("/taos/themes");
}

QString ThemeCatalog::systemDir() {
    const QString e = qEnvironmentVariable("TAOS_SYSTEM_THEME_DIR");
    return e.isEmpty() ? QStringLiteral("/usr/share/taos/themes") : e;
}

QStringList ThemeCatalog::standardDirs(bool includeUser) {
    QStringList d;
    if (!qEnvironmentVariableIsEmpty("TAOS_THEME_DIR")) d << qEnvironmentVariable("TAOS_THEME_DIR");
    if (includeUser) d << userDir();
    d << systemDir();
    return d;
}

void ThemeCatalog::scan(const QStringList &dirs, const QString &builtin) {
    initThemeResources();
    m_themes.clear();
    m_refused.clear();
    QStringList order;
    order << builtin << dirs;
    for (const QString &dir : std::as_const(order)) {
        const bool isBuiltin = dir == builtin;
        const QStringList subs = QDir(dir).entryList(QDir::Dirs | QDir::NoDotAndDotDot, QDir::Name);
        for (const QString &sub : subs) {
            const QString path = dir + QLatin1Char('/') + sub + QStringLiteral("/theme.json");
            if (!QFileInfo::exists(path)) continue;
            if (find(sub)) continue;  // an earlier source already provides this id
            ThemeParse p = parseThemeFile(path, sub);
            if (!p.ok) {
                m_refused.append({sub, path, p.message()});
                continue;
            }
            p.def.builtin = isBuiltin;
            m_themes.append(p.def);
        }
    }
    auto rank = [](const ThemeDef &d) {
        for (size_t i = 0; i < sizeof(kShippedOrder) / sizeof(*kShippedOrder); ++i)
            if (d.id == QLatin1String(kShippedOrder[i])) return int(i);
        return 1000;
    };
    std::stable_sort(m_themes.begin(), m_themes.end(), [&](const ThemeDef &a, const ThemeDef &b) {
        const int ra = rank(a), rb = rank(b);
        if (ra != rb) return ra < rb;
        return a.name.compare(b.name, Qt::CaseInsensitive) < 0;
    });
}

const ThemeDef *ThemeCatalog::find(const QString &id) const {
    for (const ThemeDef &d : m_themes)
        if (d.id == id) return &d;
    return nullptr;
}

const ThemeDef *ThemeCatalog::resolve(const QString &id) const {
    if (const ThemeDef *d = find(id)) return d;
    if (const ThemeDef *d = find(QString::fromLatin1(kDefaultId))) return d;
    return m_themes.isEmpty() ? nullptr : &m_themes.first();
}
