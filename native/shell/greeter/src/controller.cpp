// SPDX-License-Identifier: AGPL-3.0-or-later
#include "controller.h"

#include <cstdio>

GreeterController::GreeterController(GreetdClient *c, QObject *parent) : QObject(parent), m_client(c) {
    connect(c, &GreetdClient::promptRequested, this, [this](const QString &kind, const QString &text) {
        if (m_user.isEmpty()) return;  // a stale reply after back()
        if (kind == QLatin1String("info") || kind == QLatin1String("error")) {
            setMessage(kind, text.trimmed());
            return;
        }
        m_lastKind = kind;
        m_secret = kind == QLatin1String("secret");
        m_placeholder = friendlyPrompt(text);
        emit promptChanged();
        if (m_havePending) {
            const QString t = m_pending;
            m_havePending = false;
            m_pending.clear();
            m_client->respond(t);
            setPhase(Busy);
            return;
        }
        setPhase(Input);
        emit inputReady();
    });
    connect(c, &GreetdClient::authFailed, this, [this](const QString &desc, bool credentials) {
        if (m_user.isEmpty()) return;
        std::fprintf(stderr, "taos-greeter: sign in failed (%s)\n", qPrintable(desc));
        m_havePending = false;
        setMessage(QStringLiteral("error"),
                   credentials ? (m_secret ? tr("That password did not work") : tr("That code did not work"))
                               : tr("Could not sign in. Please try again"));
        emit failed();
        // Ready for the next attempt straight away.
        setPhase(Preparing);
        m_client->login(m_user);
    });
    connect(c, &GreetdClient::sessionStarting, this, [this] {
        setPhase(Done);
        emit sessionStarted();
    });
    connect(c, &GreetdClient::fatal, this, [this](const QString &why) { reportFatal(why); });
}

QString GreeterController::friendlyPrompt(const QString &text) {
    QString t = text.trimmed();
    while (t.endsWith(QLatin1Char(':'))) t.chop(1);
    t = t.trimmed();
    if (t.isEmpty() || t.compare(QLatin1String("password"), Qt::CaseInsensitive) == 0) return tr("Enter password");
    return t;
}

void GreeterController::setPhase(Phase p) {
    if (m_phase == p) return;
    m_phase = p;
    if (qEnvironmentVariableIsSet("TAOS_GREETER_DEBUG")) std::fprintf(stderr, "taos-greeter: phase %d\n", int(p));
    emit phaseChanged();
}

void GreeterController::setMessage(const QString &kind, const QString &text) {
    if (m_messageKind == kind && m_message == text) return;
    m_messageKind = kind;
    m_message = text;
    emit messageChanged();
}

void GreeterController::select(const QString &username) {
    if (m_phase == Done || m_phase == Fatal || username.isEmpty()) return;
    if (m_user == username && (m_phase == Input || m_phase == Busy || m_phase == Preparing)) return;
    m_user = username;
    m_havePending = false;
    m_placeholder = tr("Enter password");
    m_secret = true;
    emit selectedUserChanged();
    emit promptChanged();
    setMessage(QString(), QString());
    setPhase(Preparing);
    m_client->login(username);
}

void GreeterController::submit(const QString &text) {
    if (m_user.isEmpty()) return;
    if (m_phase == Input) {
        setMessage(QString(), QString());
        setPhase(Busy);
        m_client->respond(text);
    } else if (m_phase == Preparing) {
        m_pending = text;
        m_havePending = true;
        setMessage(QString(), QString());
    }
}

void GreeterController::back() {
    if (m_phase == Done || m_phase == Fatal) return;
    m_client->cancel();
    m_user.clear();
    m_havePending = false;
    emit selectedUserChanged();
    setMessage(QString(), QString());
    setPhase(Idle);
}

void GreeterController::reportFatal(const QString &message) {
    setMessage(QStringLiteral("error"), message);
    setPhase(Fatal);
    emit fatalError(message);
}
