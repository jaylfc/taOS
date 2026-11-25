// SPDX-License-Identifier: AGPL-3.0-or-later
#include "containerexec.h"

#include <QFileInfo>
#include <QProcessEnvironment>

namespace Taos::Survival {

ContainerExec::ContainerExec() : ContainerExec(qEnvironmentVariable("TAOS_SURVIVAL_EXEC")) {}
ContainerExec::ContainerExec(const QString &prefix) : m_prefix(QProcess::splitCommand(prefix)) {}

namespace {
// With no prefix the tool is the one installed in the container's own directories, never whatever PATH finds first.
QString fixedToolPath(const QString &tool)
{
    if (tool.contains(u'/'))
        return tool;
    for (const char *dir : {"/usr/bin/", "/usr/local/bin/", "/opt/taos/bin/"}) {
        const QString p = QString::fromLatin1(dir) + tool;
        if (QFileInfo::exists(p))
            return p;
    }
    return QStringLiteral("/usr/bin/") + tool;
}
} // namespace

QStringList ContainerExec::command(const QString &tool, const QStringList &args) const
{
    QStringList c = m_prefix;
    c << (m_prefix.isEmpty() ? fixedToolPath(tool) : tool) << args;   // behind a prefix the container resolves it
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
