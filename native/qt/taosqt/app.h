#pragma once
#include <QObject>
#include <QStringList>

#include "taosqt/global.h"

class QLocalServer;

namespace Taos {

/// What every native taOS app declares about itself.
///
/// Conventions (see native/qt/README.md):
///  * `appId` is reverse-DNS under the product domain: `my.taos.<name>`, for
///    example `my.taos.notifications`. It is the Wayland app_id (through the
///    desktop file name), the X11 `StartupWMClass`, the `.desktop` file base
///    name (`my.taos.notifications.desktop`) and the single-instance key. One
///    string, so the window switcher can match a window to its name and icon.
///  * The icon name in the .desktop file is the app id too, resolved from the
///    icon theme.
struct TAOSQT_EXPORT AppInfo
{
    QString appId;        ///< `my.taos.notifications`
    QString displayName;  ///< `Notifications`: what the window switcher shows
    QString version;
};

namespace App {
/// Call right after constructing the QGuiApplication / QApplication: sets the
/// application name, display name, version, organisation and, most important,
/// the desktop file name (the Wayland app_id).
TAOSQT_EXPORT void configure(const AppInfo &info);
} // namespace App

/// One running instance per app id and user. The second launch forwards its
/// arguments to the first (which should raise its window) and exits.
///
///     Taos::SingleInstance single(info.appId);
///     if (!single.acquire(app.arguments().mid(1)))
///         return 0;                              // the running instance was activated
///     connect(&single, &Taos::SingleInstance::activationRequested, window, raise);
///
/// Uses a QLocalServer in the user's runtime directory, so it works on a
/// headless host and does not depend on D-Bus. (A D-Bus activatable
/// `.desktop` file is a later refinement for launching from the shell.)
class TAOSQT_EXPORT SingleInstance : public QObject
{
    Q_OBJECT
public:
    explicit SingleInstance(const QString &appId, QObject *parent = nullptr);
    ~SingleInstance() override;

    /// True when this process is the first instance (and now listens). False
    /// when another instance was found and told to activate with `args`.
    bool acquire(const QStringList &args = {});
    /// The socket name (for tests).
    QString serverName() const { return m_name; }

Q_SIGNALS:
    void activationRequested(const QStringList &args);

private:
    QString m_name;
    QLocalServer *m_server = nullptr;
};

} // namespace Taos
