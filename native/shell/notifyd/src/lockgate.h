// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The locked-phone rule (spec 7.8, test L8). The daemon asks the controller's console
// lock state and FAILS CLOSED: only the exact answer {"locked":false,"session":<int>}
// counts as unlocked; an extra key, a wrong type, a bad status, a timeout or no answer
// all count as locked. popupForm() then says what a popup may show at the person's lock level.
#include <QByteArray>
#include <QNetworkAccessManager>
#include <QObject>
#include <QString>

namespace Taos::Notifyd {

struct LockState
{
    bool locked = true; ///< fail closed
    qint64 session = 0;
};
/// `httpStatus` and `body` of GET /auth/lock-state. Anything but 200 and the exact shape is locked.
LockState parseLockState(int httpStatus, const QByteArray &body);

enum class LockLevel { Full, Summary, Hidden };
/// "full" / "summary" / "hidden"; anything else (including empty) is Hidden: unknown means least shown.
LockLevel parseLockLevel(const QString &s);

struct PopupForm
{
    enum Kind { Full, Summary, None } kind = None;
    QString title, body; ///< empty for Summary-with-count and None
    QString summaryLine; ///< "<n> new from <device>" for Summary
};
/// What the phone shell may draw for `count` notifications: unlocked shows them in full; locked
/// follows the person's level (full / count only / nothing).
PopupForm popupForm(bool locked, LockLevel level, const QString &title, const QString &body,
                    int count, const QString &deviceLabel);

class LockGate : public QObject
{
    Q_OBJECT
public:
    LockGate(const QString &baseUrl, int timeoutMs, QObject *parent = nullptr);
    /// Asks the controller; emits changed() with the fail-closed result.
    void refresh();
    bool locked() const { return m_state.locked; }
    qint64 session() const { return m_state.session; }
Q_SIGNALS:
    void changed(bool locked);

private:
    QString m_url;
    int m_timeout;
    QNetworkAccessManager m_net;
    LockState m_state;
};

} // namespace Taos::Notifyd
