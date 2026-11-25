// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Education channels: the Learning Equality catalogue of Kolibri channels (Khan Academy and others), and the import
// of a channel into the Kolibri that runs in the survival-studio container. Kolibri downloads and checks the content
// itself from Learning Equality's servers; we only list and ask.
#include <QList>
#include <QObject>
#include <QProcess>
#include <QString>

#include "containerexec.h"

namespace Taos::Survival {

struct EduChannel
{
    QString id, name, language, description, version, lastPublished;
    qint64 bytes = 0;
    int resources = 0;
};

QList<EduChannel> parseKolibriChannels(const QByteArray &json);
bool validChannelId(const QString &id);   // 32 hex digits: nothing else is ever put on a command line

class ChannelImporter : public QObject
{
    Q_OBJECT
public:
    explicit ChannelImporter(ContainerExec *exec, QObject *parent = nullptr) : QObject(parent), m_exec(exec) {}
    /// kolibri manage importchannel network <id>, then importcontent network <id>.
    bool start(const QString &channelId, QString *error);
    void cancel();
    bool running() const { return m_proc != nullptr; }
Q_SIGNALS:
    void finished(const QString &channelId, bool ok, const QString &message);
private:
    void next();
    ContainerExec *m_exec;
    QProcess *m_proc = nullptr;
    QString m_id;
    int m_step = 0;
};

} // namespace Taos::Survival
