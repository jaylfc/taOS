// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QString>
#include <QStringList>

// How the display code talks to the compositor: one swaymsg call at a time.
// The interface exists so tests can stand in a scripted compositor.
class SwayRunner {
public:
    struct Result {
        bool ok = false;      // swaymsg ran, exited 0 and every command in its reply succeeded
        QByteArray out;       // stdout
        QString error;        // what went wrong, in words
    };
    virtual ~SwayRunner() = default;
    virtual Result run(const QStringList &args) = 0;
};

// The real thing: `swaymsg <args>` ($TAOS_SWAYMSG overrides the program).
class SwayMsg : public SwayRunner {
public:
    explicit SwayMsg(QString program = QString());
    Result run(const QStringList &args) override;
    QString program() const { return m_program; }

private:
    QString m_program;
};
