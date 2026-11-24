#include "rowmodel.h"

#include <QFileInfo>
#include <QHash>
#include <QIcon>
#include <QUrl>

using namespace taos::search;

namespace taos::launcher {

QString RowModel::kindLabel(const QString &k) {
    if (k == QLatin1String("app")) return QStringLiteral("application");
    if (k == QLatin1String("window")) return QStringLiteral("open window");
    if (k == QLatin1String("command")) return QStringLiteral("action");
    if (k == QLatin1String("calc")) return QStringLiteral("calculator result");
    if (k == QLatin1String("file")) return QStringLiteral("file");
    if (k == QLatin1String("setting")) return QStringLiteral("setting");
    if (k == QLatin1String("ask")) return QStringLiteral("ask your agent");
    if (k == QLatin1String("mention")) return QStringLiteral("agent");
    return k;
}

QString RowModel::iconUrl(const QString &icon) {
    if (icon.isEmpty()) return {};
    static QHash<QString, bool> cache;
    auto it = cache.find(icon);
    if (it == cache.end()) {
        const bool ok = icon.startsWith(QLatin1Char('/')) ? QFileInfo::exists(icon) : (QIcon::hasThemeIcon(icon) || QIcon::hasThemeIcon(icon.toLower()));
        it = cache.insert(icon, ok);
    }
    if (!*it) return {};
    return QStringLiteral("image://icon/") + QString::fromLatin1(QUrl::toPercentEncoding(icon));
}

QHash<int, QByteArray> RowModel::roleNames() const {
    return { {SectionRole, "section"}, {FirstInSectionRole, "firstInSection"}, {TitleRole, "title"}, {SubtitleRole, "subtitle"},
             {IconRole, "icon"}, {HasIconRole, "hasIcon"}, {KindRole, "kind"}, {HintRole, "hint"}, {IdRole, "rid"},
             {AccessibleRole, "accessibleText"}, {TopHitRole, "topHit"} };
}

QVariant RowModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() >= m_rows.size()) return {};
    const Row &r = m_rows[i.row()];
    switch (role) {
    case SectionRole: return r.section;
    case FirstInSectionRole: return i.row() == 0 || m_rows[i.row() - 1].section != r.section;
    case TitleRole: return r.r.title;
    case SubtitleRole: return r.r.subtitle;
    case IconRole: return iconUrl(r.r.icon);
    case HasIconRole: return !iconUrl(r.r.icon).isEmpty();
    case KindRole: return r.r.kind;
    case HintRole: return r.r.actions.isEmpty() ? QString() : r.r.actions.first().shownHint();
    case IdRole: return r.r.id;
    case TopHitRole: return r.section == QLatin1String("Top hit");
    case AccessibleRole: return QStringLiteral("%1, %2, %3 of %4").arg(r.r.title, kindLabel(r.r.kind)).arg(i.row() + 1).arg(m_rows.size());
    }
    return {};
}

void RowModel::setRows(const QList<Row> &rows) {
    beginResetModel();
    m_rows = rows;
    endResetModel();
}

int RowModel::indexOfId(const QString &id) const {
    for (int i = 0; i < m_rows.size(); ++i) if (m_rows[i].r.id == id) return i;
    return -1;
}

}  // namespace taos::launcher
