#pragma once
#include <QDBusConnection>
#include <QObject>
#include <memory>

#include "aboutinfo.h"
#include "audio.h"
#include "bluezclient.h"
#include "catalog.h"
#include "displaypage.h"
#include "filesettings.h"
#include "glass.h"
#include "navigator.h"
#include "nmclient.h"
#include "pulsebackend.h"
#include "theme.h"
#include "thememanager.h"

namespace TaosSettings {

// Everything a page needs, injected, so tests and renders run against temporary folders and fake engines and
// never against the owner's live state. `Options::real()` is the production wiring (the person's own files, the
// compositor's swaymsg, the sound server and the system bus).
struct Options {
    ThemeManager::Options theme;
    QString dockPath, desktopPath;
    SwayRunner *sway = nullptr;            // borrowed; null: a real SwayMsg is created
    QString displayConf;                   // empty: DisplayConfigFile::defaultPath()
    QString drmRoot = QStringLiteral("/sys/class/drm");
    std::function<qint64()> displayClock;
    bool watch = true;                     // follow files
    bool watchOutputs = true;              // follow monitor events (swaymsg subscribe)
    AudioBackend *audioBackend = nullptr;  // borrowed; null: PulseBackend on the user's server
    QString pulseServer;
    QString dbusAddress;                   // empty: the system bus
    QString osRoot, storagePath;
    bool useDbus = true;
};

class SettingsContext : public QObject {
    Q_OBJECT
    Q_PROPERTY(QObject *catalog READ catalogObject CONSTANT)
    Q_PROPERTY(QObject *nav READ navObject CONSTANT)
    Q_PROPERTY(QObject *themes READ themesObject CONSTANT)
    Q_PROPERTY(QObject *dock READ dockObject CONSTANT)
    Q_PROPERTY(QObject *desktop READ desktopObject CONSTANT)
    Q_PROPERTY(QObject *display READ displayObject CONSTANT)
    Q_PROPERTY(QObject *audio READ audioObject CONSTANT)
    Q_PROPERTY(QObject *wifi READ wifiObject CONSTANT)
    Q_PROPERTY(QObject *bluetooth READ bluetoothObject CONSTANT)
    Q_PROPERTY(QObject *about READ aboutObject CONSTANT)
    Q_PROPERTY(QVariantList shortcuts READ shortcuts CONSTANT)
public:
    explicit SettingsContext(const Options &o, QObject *parent = nullptr);
    ~SettingsContext() override;

    Catalog *catalog() { return &m_catalog; }
    Navigator *nav() { return &m_nav; }
    ThemeManager *themes() { return m_themes.get(); }
    DockSettings *dock() { return &m_dock; }
    DesktopSettings *desktop() { return &m_desktop; }
    DisplayPage *display() { return m_display.get(); }
    Audio *audio() { return m_audio.get(); }
    NmClient *wifi() { return m_nm.get(); }
    BluezClient *bluetooth() { return m_bluez.get(); }
    AboutInfo *about() { return &m_about; }
    Theme *theme() { return &m_theme; }
    Style *style() { return &m_style; }
    QVariantList shortcuts() const;

    QObject *catalogObject() { return &m_catalog; }
    QObject *navObject() { return &m_nav; }
    QObject *themesObject() { return m_themes.get(); }
    QObject *dockObject() { return &m_dock; }
    QObject *desktopObject() { return &m_desktop; }
    QObject *displayObject() { return m_display.get(); }
    QObject *audioObject() { return m_audio.get(); }
    QObject *wifiObject() { return m_nm.get(); }
    QObject *bluetoothObject() { return m_bluez.get(); }
    QObject *aboutObject() { return &m_about; }

    // The page that is showing asks for the engines it needs, and lets go when it is hidden.
    Q_INVOKABLE void setNetworkActive(bool on);
    // The first row of a list model whose boolean role `active` is true (-1: none). For the pickers.
    Q_INVOKABLE int activeRow(QObject *model) const;
    // Say `text` to assistive technology (a changed setting, a state change). `item` is any visible QML item or window.
    Q_INVOKABLE void announce(QObject *item, const QString &text) const;

private:
    Catalog m_catalog;
    Navigator m_nav;
    Theme m_theme;
    Style m_style;
    std::unique_ptr<ThemeManager> m_themes;
    DockSettings m_dock;
    DesktopSettings m_desktop;
    std::unique_ptr<SwayRunner> m_ownSway;
    std::unique_ptr<DisplayPage> m_display;
    std::unique_ptr<AudioBackend> m_ownAudio;
    std::unique_ptr<Audio> m_audio;
    QDBusConnection m_bus;
    std::unique_ptr<NmClient> m_nm;
    std::unique_ptr<BluezClient> m_bluez;
    AboutInfo m_about;
    bool m_dbus;
};

} // namespace TaosSettings
