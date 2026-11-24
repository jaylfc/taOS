// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QStringList>

#include "catalog.h"

namespace TaosSettings {

// Where the window is: the section shown and the setting asked for (a deep link highlights it).
// The only state of the app that is not the engines' own.
class Navigator : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString section READ section NOTIFY changed)
    Q_PROPERTY(QString setting READ setting NOTIFY changed)
    Q_PROPERTY(QString query READ query WRITE setQuery NOTIFY queryChanged)
public:
    explicit Navigator(const Catalog *catalog, QObject *parent = nullptr);
    QString section() const { return m_section; }
    QString setting() const { return m_setting; }
    QString query() const { return m_query; }
    void setQuery(const QString &q);

    // False (and no change) for an unknown section or setting. A setting alone implies its section.
    Q_INVOKABLE bool navigate(const QString &section, const QString &setting = QString());
    // `--section <id>`, `--section=<id>`, `--setting <id>`, `--search <text>`. Returns true when it moved.
    bool applyArguments(const QStringList &args);
    Q_INVOKABLE void clearSetting();

signals:
    void changed();
    void queryChanged();
    // The app should come to the front and show `setting` (a deep link from outside).
    void revealRequested(const QString &setting);

private:
    const Catalog *m_catalog;
    QString m_section, m_setting, m_query;
};

} // namespace TaosSettings
