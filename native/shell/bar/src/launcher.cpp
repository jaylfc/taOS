// SPDX-License-Identifier: AGPL-3.0-or-later
#include "launcher.h"

#include <QProcess>
#include <QProcessEnvironment>
#include <QDebug>

void Launcher::setCommand(const QString &c) {
    if (c == m_cmd) return;
    m_cmd = c;
    emit commandChanged();
}

bool Launcher::launch() {
    QStringList parts = QProcess::splitCommand(m_cmd);
    if (parts.isEmpty()) return false;
    QProcess p;
    p.setProgram(parts.takeFirst());
    p.setArguments(parts);
    // The bar itself runs with the layer-shell integration forced on; a child
    // Qt app must not inherit that or it would try to become a layer surface.
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    env.remove(QStringLiteral("QT_WAYLAND_SHELL_INTEGRATION"));
    p.setProcessEnvironment(env);
    qint64 pid = 0;
    const bool ok = p.startDetached(&pid);
    if (!ok) qWarning().noquote() << "taos-bar: could not start launcher command:" << m_cmd;
    return ok;
}
