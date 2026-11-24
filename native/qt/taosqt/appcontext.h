// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Needs Qt Qml (TAOSQT_WITH_QML).
#include <QJSValue>
#include <QObject>
#include <QVariantMap>

#include "taosqt/session.h"

class QJSEngine;

namespace Taos {

/// The `Taos` singleton of the QML module `Taos 1.0`, created by the generated
/// app entry point. Gives QML everything without per-app C++:
///
///     import Taos 1.0
///     Text { text: Taos.session.userName }
///     Component.onCompleted: Taos.call("notifications.mark_all_read", {}, function (ok, value, error) { ... })
///     LiveQuery { op: "decisions.list"; running: Taos.session.state === Session.SignedIn }
class TAOSQT_EXPORT AppContext : public QObject
{
    Q_OBJECT
    Q_PROPERTY(Taos::Session *session READ session CONSTANT)
    Q_PROPERTY(Taos::Client *client READ client CONSTANT)
    Q_PROPERTY(QString appId READ appId CONSTANT)
    /// The shell's `Style` (native/shell/common: colours, glass, dark, reduceMotion); null without it.
    Q_PROPERTY(QObject *style READ style CONSTANT)
    /// The command line of this process, without the program name.
    Q_PROPERTY(QStringList arguments READ arguments CONSTANT)
public:
    AppContext(Session *session, const QString &appId, QObject *parent = nullptr);
    Session *session() const { return m_session; }
    Client *client() const { return m_session->client(); }
    QString appId() const { return m_appId; }
    void setEngine(QJSEngine *engine) { m_engine = engine; }
    QObject *style() const { return m_style; }
    void setStyle(QObject *style) { m_style = style; }
    QStringList arguments() const;
    /// A second launch forwarded its command line here (the window is raised too).
    void announceActivation(const QStringList &args) { Q_EMIT activated(args); }

    /// Run an operation (see taos_client.h). `callback(ok, value, errorText)`
    /// is called once on the GUI thread; `errorText` is the sentence to show.
    Q_INVOKABLE void call(const QString &op, const QVariantMap &args, const QJSValue &callback);
    /// A raw request; same callback.
    Q_INVOKABLE void http(const QString &method, const QString &path, const QVariantMap &query,
                          const QVariant &body, const QJSValue &callback);

Q_SIGNALS:
    /// A second launch of this app forwarded its command line (after the program name; for example
    /// `--agent <name>`). The window is raised too; an app that navigates on launch reads them here
    /// and reads its own first launch from `arguments`.
    void activated(const QStringList &args);

private:
    void deliver(Reply *r, const QJSValue &callback);

    Session *m_session;
    QString m_appId;
    QJSEngine *m_engine = nullptr;
    QObject *m_style = nullptr;
};

} // namespace Taos
