// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QList>
#include <QObject>
#include <QString>
#include <QStringList>
#include <QVariantList>

namespace TaosSettings {

// A section is a page of the sidebar. A page is a QML component; later sections and third party
// panels (a declarative `settings.panel` form schema) register the same way: id, title, icon, component, keywords.
struct SectionEntry {
    QString id, title, icon, component;
    QStringList keywords;
};

// A setting declares its title, keywords and section. The sidebar search and the deep links
// (`taos-settings --section <id> --setting <id>`) both read this one table; the QML rows only
// name their setting id and ask the catalog for the words.
struct SettingEntry {
    QString id, section, title, description;
    QStringList keywords;
};

class Catalog : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList sections READ sections NOTIFY changed)
public:
    explicit Catalog(QObject *parent = nullptr) : QObject(parent) {}
    // The first-party sections and settings.
    static Catalog *withBuiltins(QObject *parent = nullptr);

    void addSection(const SectionEntry &s);
    void addSetting(const SettingEntry &s);
    const QList<SectionEntry> &sectionList() const { return m_sections; }
    const QList<SettingEntry> &settingList() const { return m_settings; }

    QVariantList sections() const;
    Q_INVOKABLE bool hasSection(const QString &id) const;
    Q_INVOKABLE QString sectionTitle(const QString &id) const;
    Q_INVOKABLE QString component(const QString &sectionId) const;
    Q_INVOKABLE QString sectionOf(const QString &settingId) const;
    Q_INVOKABLE QString title(const QString &settingId) const;
    Q_INVOKABLE QString description(const QString &settingId) const;
    // Search as you type: every word of `query` must be found in the title, description, keywords or
    // section name. Titles beat keywords. Result rows: {kind: "setting"|"section", id, section,
    // sectionTitle, title, description}. An empty query returns nothing.
    Q_INVOKABLE QVariantList search(const QString &query) const;

signals:
    void changed();

private:
    QList<SectionEntry> m_sections;
    QList<SettingEntry> m_settings;
};

} // namespace TaosSettings
