// SPDX-License-Identifier: AGPL-3.0-or-later
#include "outputwatcher.h"

OutputWatcher::OutputWatcher(QString program, int debounceMs, QObject *parent) : QObject(parent), m_program(std::move(program)) {
    if (m_program.isEmpty()) m_program = qEnvironmentVariable("TAOS_SWAYMSG", QStringLiteral("swaymsg"));
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(debounceMs);
    connect(&m_debounce, &QTimer::timeout, this, &OutputWatcher::outputsChanged);
    m_restart.setSingleShot(true);
    m_restart.setInterval(2000);
    connect(&m_restart, &QTimer::timeout, this, &OutputWatcher::start);
    connect(&m_proc, &QProcess::readyReadStandardOutput, this, &OutputWatcher::onReadyRead);
    connect(&m_proc, &QProcess::finished, this, &OutputWatcher::onFinished);
}

void OutputWatcher::start() {
    m_stopping = false;
    if (m_proc.state() != QProcess::NotRunning) return;
    m_proc.start(m_program, {QStringLiteral("-t"), QStringLiteral("subscribe"), QStringLiteral("-m"), QStringLiteral("[\"output\"]")});
}

void OutputWatcher::stop() {
    m_stopping = true;
    m_restart.stop();
    m_debounce.stop();
    if (m_proc.state() != QProcess::NotRunning) { m_proc.terminate(); m_proc.waitForFinished(1000); }
}

void OutputWatcher::onReadyRead() {
    m_buf += m_proc.readAllStandardOutput();
    int nl;
    while ((nl = m_buf.indexOf('\n')) >= 0) {
        if (nl > 0) m_debounce.start();  // restarts the wait: one signal after the burst
        m_buf.remove(0, nl + 1);
    }
}

void OutputWatcher::onFinished() {
    if (!m_stopping) m_restart.start();  // the compositor restarted or the pipe broke: resubscribe
}
