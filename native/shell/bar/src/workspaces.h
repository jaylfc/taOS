// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonArray>
#include <QObject>
#include <QTimer>
#include <QVariantList>

class SwayRunner;

// The workspaces of the compositor for the strip bar's tick marks: [{name, focused}]. Read once a second while
// anything listens, from `swaymsg -t get_workspaces` (never writes). Empty when there is no compositor to ask.
class Workspaces : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList list READ list NOTIFY changed)
public:
    explicit Workspaces(SwayRunner *runner, QObject *parent = nullptr);
    QVariantList list() const { return m_list; }
    void start(int intervalMs = 1000);
    void refresh();
    // For tests: parse the reply of get_workspaces.
    static QVariantList parse(const QByteArray &json);
signals:
    void changed();

private:
    SwayRunner *m_runner;
    QTimer m_timer;
    QVariantList m_list;
};
