// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QObject>
#include <QProcess>
#include <QTimer>

// `swaymsg -t subscribe -m '["output"]'`: says when a monitor is plugged in,
// unplugged or reconfigured. Events that arrive close together (a mode change
// produces several) become one `outputsChanged`, `debounceMs` after the last.
class OutputWatcher : public QObject {
    Q_OBJECT
public:
    explicit OutputWatcher(QString program = QString(), int debounceMs = 500, QObject *parent = nullptr);
    void start();
    void stop();
signals:
    void outputsChanged();
private:
    void onReadyRead();
    void onFinished();
    QString m_program;
    QProcess m_proc;
    QTimer m_debounce, m_restart;
    QByteArray m_buf;
    bool m_stopping = false;
};
