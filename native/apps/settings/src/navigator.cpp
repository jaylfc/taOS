#include "navigator.h"

using namespace Qt::StringLiterals;

namespace TaosSettings {

Navigator::Navigator(const Catalog *catalog, QObject *parent) : QObject(parent), m_catalog(catalog)
{
    if (!m_catalog->sectionList().isEmpty())
        m_section = m_catalog->sectionList().first().id;
}

void Navigator::setQuery(const QString &q)
{
    if (q == m_query)
        return;
    m_query = q;
    emit queryChanged();
}

bool Navigator::navigate(const QString &section, const QString &setting)
{
    QString sec = section;
    if (!setting.isEmpty()) {
        const QString owner = m_catalog->sectionOf(setting);
        if (owner.isEmpty())
            return false;
        if (sec.isEmpty())
            sec = owner;
        else if (sec != owner)
            return false;
    }
    if (!m_catalog->hasSection(sec))
        return false;
    m_section = sec;
    m_setting = setting;
    if (!m_query.isEmpty()) {
        m_query.clear();
        emit queryChanged();
    }
    emit changed();
    if (!setting.isEmpty())
        emit revealRequested(setting);
    return true;
}

void Navigator::clearSetting()
{
    if (m_setting.isEmpty())
        return;
    m_setting.clear();
    emit changed();
}

bool Navigator::applyArguments(const QStringList &args)
{
    QString section, setting, search;
    for (int i = 0; i < args.size(); ++i) {
        const QString &a = args.at(i);
        const auto value = [&](const QString &name, QString *out) {
            if (a == name && i + 1 < args.size()) { *out = args.at(++i); return true; }
            if (a.startsWith(name + u'=')) { *out = a.mid(name.size() + 1); return true; }
            return false;
        };
        if (value(u"--section"_s, &section) || value(u"--setting"_s, &setting) || value(u"--search"_s, &search))
            continue;
    }
    bool moved = false;
    if (!section.isEmpty() || !setting.isEmpty())
        moved = navigate(section, setting);
    if (!search.isEmpty()) {
        setQuery(search);
        moved = true;
    }
    return moved;
}

} // namespace TaosSettings
