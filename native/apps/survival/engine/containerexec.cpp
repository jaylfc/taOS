// SPDX-License-Identifier: AGPL-3.0-or-later
#include "containerexec.h"

#include <QProcessEnvironment>

namespace Taos::Survival {

ContainerExec::ContainerExec() : ContainerExec(qEnvironmentVariable("TAOS_SURVIVAL_EXEC")) {}
ContainerExec::ContainerExec(const QString &prefix) : m_prefix(QProcess::splitCommand(prefix)) {}

QStringList ContainerExec::command(const QString &tool, const QStringList &args) const
{
    QStringList c = m_prefix;
    c << tool << args;
    return c;
}

QProcess *ContainerExec::makeProcess(const QString &tool, const QStringList &args, QObject *parent) const
{
    auto *p = new QProcess(parent);
    const QStringList c = command(tool, args);
    p->setProgram(c.first());
    p->setArguments(c.mid(1));
    p->setProcessChannelMode(QProcess::MergedChannels);
    return p;
}

} // namespace Taos::Survival
