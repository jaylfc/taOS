// SPDX-License-Identifier: AGPL-3.0-or-later
#include "context.h"

#include <QAbstractItemModel>
#include <QAccessible>

#include "shortcuts.h"

using namespace Qt::StringLiterals;

namespace TaosSettings {

SettingsContext::SettingsContext(const Options &o, QObject *parent)
    : QObject(parent), m_catalog(), m_nav(&m_catalog), m_style(&m_theme),
      m_dock(o.dockPath.isEmpty() ? DockSettings::defaultPath() : o.dockPath, o.watch),
      m_desktop(o.desktopPath.isEmpty() ? DesktopSettings::defaultPath() : o.desktopPath, o.watch),
      m_bus(QDBusConnection::systemBus()), m_about(o.osRoot, o.storagePath), m_dbus(o.useDbus)
{
    // Catalog: the first-party sections and settings.
    {
        std::unique_ptr<Catalog> built(Catalog::withBuiltins());
        for (const SectionEntry &s : built->sectionList())
            m_catalog.addSection(s);
        for (const SettingEntry &s : built->settingList())
            m_catalog.addSetting(s);
    }
    m_nav.navigate(m_catalog.sectionList().first().id);

    m_themes = std::make_unique<ThemeManager>(&m_theme, o.theme);
    QString err;
    if (!m_themes->start(&err))
        qWarning("taos-settings: theme not loaded (%s)", qUtf8Printable(err));

    SwayRunner *sway = o.sway;
    if (!sway) {
        m_ownSway = std::make_unique<SwayMsg>();
        sway = m_ownSway.get();
    }
    DisplayConfigFile cfg = o.displayConf.isEmpty() ? DisplayConfigFile() : DisplayConfigFile(o.displayConf);
    m_display = std::make_unique<DisplayPage>(sway, cfg, o.drmRoot, o.displayClock, o.watchOutputs);

    AudioBackend *backend = o.audioBackend;
    if (!backend) {
        m_ownAudio = std::make_unique<PulseBackend>(o.pulseServer);
        backend = m_ownAudio.get();
    }
    m_audio = std::make_unique<Audio>(backend);

    if (!o.dbusAddress.isEmpty())
        m_bus = QDBusConnection::connectToBus(o.dbusAddress, u"taos-settings-net"_s);
    m_nm = std::make_unique<NmClient>(m_bus);
    m_bluez = std::make_unique<BluezClient>(m_bus);
    if (o.useDbus) {
        m_nm->start();
        m_bluez->start();
    }
}

SettingsContext::~SettingsContext() = default;

QVariantList SettingsContext::shortcuts() const { return shellShortcuts(); }

void SettingsContext::setNetworkActive(bool on)
{
    m_nm->setActive(on);
    m_bluez->setActive(on);
}

int SettingsContext::activeRow(QObject *model) const
{
    auto *m = qobject_cast<QAbstractItemModel *>(model);
    if (!m)
        return -1;
    const QHash<int, QByteArray> roles = m->roleNames();
    int role = -1;
    for (auto it = roles.begin(); it != roles.end(); ++it)
        if (it.value() == "active")
            role = it.key();
    if (role < 0)
        return -1;
    for (int i = 0; i < m->rowCount(); ++i)
        if (m->data(m->index(i, 0), role).toBool())
            return i;
    return -1;
}

void SettingsContext::announce(QObject *item, const QString &text) const
{
    if (!item || text.isEmpty() || !QAccessible::isActive())
        return;
    QAccessibleAnnouncementEvent ev(item, text);
    QAccessible::updateAccessibility(&ev);
}

} // namespace TaosSettings
