// SPDX-License-Identifier: AGPL-3.0-or-later
#include "displaypage.h"

namespace TaosSettings {

DisplayPage::DisplayPage(SwayRunner *sway, const DisplayConfigFile &cfg, const QString &drmRoot, std::function<qint64()> nowMs,
                         bool watch, QObject *parent)
    : QObject(parent), m_sway(sway), m_ctl(sway, cfg, drmRoot, std::move(nowMs))
{
    connect(&m_ctl, &DisplayController::outputsChanged, this, [this] { update(true); });
    if (watch) {
        m_watcher = std::make_unique<OutputWatcher>();
        connect(m_watcher.get(), &OutputWatcher::outputsChanged, this, [this] { if (!m_ctl.trialActive()) reload(); });
        m_watcher->start();
    }
    reload();
}

void DisplayPage::reload()
{
    update(m_ctl.refresh());
}

void DisplayPage::update(bool ok)
{
    QString state, msg;
    if (!ok) {
        state = QStringLiteral("error");
        msg = tr("The screens could not be read. They are managed by the desktop's compositor, which is not answering here.");
    } else if (m_ctl.outputs().isEmpty()) {
        state = QStringLiteral("empty");
        msg = tr("No screen is connected.");
    } else {
        state = QStringLiteral("ready");
    }
    if (state != m_state || msg != m_message) {
        m_state = state;
        m_message = msg;
        emit stateChanged();
    }
}

} // namespace TaosSettings
