// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Where the tools run. The survival-studio container holds pmtiles and Kolibri; the engine reaches them through one
// prefix (TAOS_SURVIVAL_EXEC, for example "podman exec survival-studio"). With no prefix the tool is run directly,
// which is right when the engine itself runs inside the container.
#include <QProcess>
#include <QString>
#include <QStringList>

namespace Taos::Survival {

class ContainerExec
{
public:
    ContainerExec();
    explicit ContainerExec(const QString &prefix);
    /// The program and arguments that will really be run for `tool args...`.
    QStringList command(const QString &tool, const QStringList &args) const;
    /// A process ready to start (not started).
    QProcess *makeProcess(const QString &tool, const QStringList &args, QObject *parent = nullptr) const;
    QString prefix() const { return m_prefix.join(QLatin1Char(' ')); }
private:
    QStringList m_prefix;
};

} // namespace Taos::Survival
