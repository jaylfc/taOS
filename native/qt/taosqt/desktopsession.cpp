// SPDX-License-Identifier: AGPL-3.0-or-later
#include "taosqt/desktopsession.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSaveFile>
#include <QStandardPaths>

#include <unistd.h>

using namespace Qt::StringLiterals;

namespace Taos {

DesktopSessionTokenSource::DesktopSessionTokenSource(const QString &runtimeDir, QObject *parent)
    : TokenSource(parent)
{
    const QString base = runtimeDir.isEmpty() ? qEnvironmentVariable("XDG_RUNTIME_DIR") : runtimeDir;
    if (!base.isEmpty())
        m_path = QDir(base).filePath(u"taos/session"_s);
    connect(&m_watcher, &QFileSystemWatcher::directoryChanged, this, &DesktopSessionTokenSource::changed);
    connect(&m_watcher, &QFileSystemWatcher::fileChanged, this, &DesktopSessionTokenSource::changed);
}

QByteArray DesktopSessionTokenSource::read() const
{
    if (m_path.isEmpty())
        return {};
    const QFileInfo fi(m_path);
    if (!fi.exists() || !fi.isFile() || fi.isSymLink())
        return {};
    // Never trust a file someone else owns or can read, nor one in a directory
    // someone else owns or can list or write (they could swap the file).
    constexpr auto others = QFileDevice::ReadGroup | QFileDevice::WriteGroup | QFileDevice::ExeGroup
        | QFileDevice::ReadOther | QFileDevice::WriteOther | QFileDevice::ExeOther;
    if (fi.ownerId() != ::getuid() || (fi.permissions() & others))
        return {};
    const QFileInfo di(fi.absolutePath());
    if (!di.isDir() || di.isSymLink() || di.ownerId() != ::getuid() || (di.permissions() & others))
        return {};
    QFile f(m_path);
    return f.open(QIODevice::ReadOnly) ? f.readAll().trimmed() : QByteArray();
}

void DesktopSessionTokenSource::acquire(Session *session, const Error &rejected)
{
    m_session = session;
    if (!m_path.isEmpty()) {
        // The directory must exist to be watched (another app may sign in later).
        const QString dir = QFileInfo(m_path).absolutePath();
        QDir().mkpath(dir);
        QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
        if (!m_watcher.directories().contains(dir))
            m_watcher.addPath(dir);
    }

    if (rejected.isError() && !m_offered.isEmpty()) {
        // What we offered was refused: remember it and drop the stale file,
        // unless another app has already replaced it with a newer session.
        m_rejected = m_offered;
        if (read() == m_rejected)
            QFile::remove(m_path);
    }
    const QByteArray saved = read();
    if (!saved.isEmpty() && saved != m_rejected) {
        m_offered = saved;
        session->useSavedSession(saved);
        return;
    }
    m_offered.clear();
    Q_EMIT inputRequired(rejected.isError() ? rejected.userMessage() : QString());
}

void DesktopSessionTokenSource::remember(const QByteArray &sessionJson)
{
    if (m_path.isEmpty() || sessionJson.isEmpty())
        return;
    const QString dir = QFileInfo(m_path).absolutePath();
    QDir().mkpath(dir);
    QFile::setPermissions(dir, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    QSaveFile f(m_path);
    f.setDirectWriteFallback(false);
    if (!f.open(QIODevice::WriteOnly))
        return;
    f.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    f.write(sessionJson);
    clearSignedOut();
    m_written = sessionJson.trimmed();
    m_offered = m_written;
    if (f.commit() && !m_watcher.directories().contains(dir))
        m_watcher.addPath(dir);
}

bool DesktopSessionTokenSource::signedOut() const
{
    return !m_path.isEmpty() && QFileInfo::exists(QFileInfo(m_path).absolutePath() + u"/signed-out"_s);
}

void DesktopSessionTokenSource::clearSignedOut()
{
    if (!m_path.isEmpty())
        QFile::remove(QFileInfo(m_path).absolutePath() + u"/signed-out"_s);
}

void DesktopSessionTokenSource::forget()
{
    if (!m_path.isEmpty()) {
        QFile::remove(m_path);
        QFile mark(QFileInfo(m_path).absolutePath() + u"/signed-out"_s);
        if (mark.open(QIODevice::WriteOnly))
            mark.setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner);
    }
    m_offered.clear();
    m_written.clear();
}

void DesktopSessionTokenSource::changed()
{
    if (!m_session || m_session->state() != Session::SignedOut)
        return;
    const QByteArray now = read();
    if (now.isEmpty() || now == m_rejected || now == m_written)
        return;
    // Another app signed in: use its session instead of asking again.
    m_session->start();
}

} // namespace Taos
