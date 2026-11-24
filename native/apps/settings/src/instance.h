// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QStringList>

class QLocalServer;

namespace TaosSettings {

// One Settings window per person. The socket lives at $XDG_RUNTIME_DIR/taos/settings.sock (dir 0700), so the
// launcher, the bar menus and the dock can open a page: a second launch sends its arguments to the first and exits.
// Protocol: one JSON array of strings per line.
class InstanceGuard : public QObject {
    Q_OBJECT
public:
    explicit InstanceGuard(QObject *parent = nullptr);
    ~InstanceGuard() override;
    static QString defaultSocketPath();
    QString socketPath() const { return m_path; }
    // True when this process is the first instance (and listens). False when the running instance got `args`.
    bool acquire(const QStringList &args);
signals:
    void activated(const QStringList &args);
private:
    QString m_path;
    QLocalServer *m_server = nullptr;
};

} // namespace TaosSettings
