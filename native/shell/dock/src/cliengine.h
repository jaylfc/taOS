// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QProcess>
#include <QTimer>

#include "windowengine.h"

// Drives the real window engine through its command line (`taos-window`). Used when TAOS_DOCK_ENGINE=cli
// (or `taos-window` is on PATH and TAOS_DOCK_ENGINE=auto). Same contract as the library.
class CliEngine : public WindowEngine {
    Q_OBJECT
public:
    explicit CliEngine(const QString &program = QStringLiteral("taos-window"), QObject *parent = nullptr);
    ~CliEngine() override;
    QString name() const override { return QStringLiteral("taos-window"); }
    void start() override;
    void refresh() override;
    QVector<WindowInfo> windows() const override { return m_windows; }
    void close(int id) override { verb(QStringLiteral("close"), id); }
    void minimise(int id) override { verb(QStringLiteral("minimise"), id); }
    void restore(int id) override { verb(QStringLiteral("restore"), id); }
    void toggleMinimise(int id) override { verb(QStringLiteral("toggle-minimise"), id); }
    void focus(int id) override { verb(QStringLiteral("focus"), id); }
    void full(int id) override { verb(QStringLiteral("full"), id); }
private:
    void verb(const QString &v, int id);
    void startSubscribe();
    QString m_prog;
    QVector<WindowInfo> m_windows;
    bool m_have = false;
    QProcess *m_sub = nullptr;
    QTimer m_debounce;
};
