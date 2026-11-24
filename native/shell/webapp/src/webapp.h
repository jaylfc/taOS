#pragma once
// taos_webapp_core: the web apps engine. A web app INSTANCE is a site opened in its own browser window with its
// own private profile (own storage, own sign-in). This library has no UI; the `taos-webapp` CLI, the Store and
// Settings are faces over it.
//
// On disk (per person, outside the OS, survives updates):
//   $XDG_DATA_HOME/taos/webapps/index.json               the registry, written atomically
//   $XDG_DATA_HOME/taos/webapps/<id>/{manifest.json, icon.*, env, profile/}
//   $XDG_DATA_HOME/applications/taos-webapp-<id>.desktop  one launcher entry per instance
#include <QDateTime>
#include <QJsonArray>
#include <QJsonObject>
#include <QList>
#include <QString>
#include <QUrl>
#include <functional>

namespace TaosWebapp {

/// The Wayland app_id Chromium gives a `--app=URL` window opened with `--profile-directory=<profileDir>`:
/// `chrome-<host>__<path with / as _>-<profile directory name>` (measured: https://www.youtube.com in Default gives
/// `chrome-www.youtube.com__-Default`). Chromium ignores `--class` for these windows, so this is the only id the
/// window has, and what StartupWMClass must say. Port, query and fragment do not take part. Characters Chromium
/// replaces in a file name (\ / : * ? " < > |) become `_`.
QString expectedAppId(const QUrl &url, const QString &profileDir);

/// One web app instance.
struct Instance {
    QString id;        ///< generated, [a-z0-9]{10}; never derived from user text
    QString name;      ///< the label shown in the dock, launcher and Settings
    QString url;       ///< the page the window opens
    QString icon;      ///< absolute path of the icon file ("" = none)
    QString source;    ///< store id ("youtube") or "custom"
    QString browser;   ///< "auto" or the name of a browser binary
    QString profile = QStringLiteral("Default");   ///< the profile directory name inside profile/: "Default" for the
                                                   ///< first instances, the instance id for every new one and clone
    QDateTime created;
    QDateTime lastUsed;
    QJsonObject toJson() const;
    static Instance fromJson(const QJsonObject &o);
    /// The window's Wayland app_id and the StartupWMClass of its launcher entry.
    QString appId() const { return expectedAppId(QUrl(url), profile); }
};

/// What every command returns. `code` is stable for tests and the CLI exit mapping:
/// "ok", "invalid_url", "invalid_id", "not_found", "no_browser", "running", "io", "invalid_icon", "invalid".
struct Result {
    bool ok = true;
    QString code = QStringLiteral("ok");
    QString error;          ///< the sentence to show
    QJsonObject data;
    static Result fail(const QString &code, const QString &error) { Result r; r.ok = false; r.code = code; r.error = error; return r; }
    static Result success(const QJsonObject &d = {}) { Result r; r.data = d; return r; }
};

struct FetchResult {
    bool ok = false;
    QByteArray data;
    QString contentType;
    QUrl finalUrl;
    QString error;
};
/// Fetches a URL (blocking). Injected so tests never touch the network.
using Fetcher = std::function<FetchResult(const QUrl &)>;
/// `taos-theme get --json` variant, and whether a colour scheme portal is installed (the Options defaults).
QString taosColorScheme();
bool portalInstalled();

/// The real fetcher (QNetworkAccessManager, 8 s, 2 MB cap, https and http only).
Fetcher networkFetcher();

/// What a browser backend needs to open an instance.
struct LaunchSpec {
    QString program;
    QStringList arguments;
};

/// A way to open an instance. Today: Chromium family on PATH. Later: a native Qt WebEngine window.
class BrowserBackend {
public:
    virtual ~BrowserBackend() = default;
    virtual QString name() const = 0;
    /// The binary found ("" = none).
    virtual QString probe(const QString &preferred) const = 0;
    virtual QStringList candidates() const = 0;
    /// Command line to open `inst` with profile `profileDir`. `program` comes from probe().
    virtual LaunchSpec launchSpec(const QString &program, const Instance &inst, const QString &profileDir) const = 0;
    /// A pid recorded by the browser itself for this profile (Chromium: SingletonLock), 0 = none.
    virtual qint64 profileOwnerPid(const QString &profileDir) const = 0;
};

/// Chromium, Chrome and Brave: `--app=URL` windows with `--user-data-dir`.
class ChromiumBackend : public BrowserBackend {
public:
    QString name() const override { return QStringLiteral("chromium"); }
    QString probe(const QString &preferred) const override;
    QStringList candidates() const override;
    LaunchSpec launchSpec(const QString &program, const Instance &inst, const QString &profileDir) const override;
    qint64 profileOwnerPid(const QString &profileDir) const override;
};

struct Options {
    QString root;              ///< webapps folder; default $XDG_DATA_HOME/taos/webapps
    QString applicationsDir;   ///< default $XDG_DATA_HOME/applications
    QString execName = QStringLiteral("taos-webapp");   ///< what the .desktop Exec runs (a wrapper on PATH)
    Fetcher fetcher;           ///< default: networkFetcher()
    BrowserBackend *backend = nullptr;   ///< default: a ChromiumBackend owned by the engine
    std::function<QDateTime()> clock;
    /// The taOS appearance in effect now: "dark", "light" or "" when unknown. Default: `taos-theme get --json`.
    std::function<QString()> colorScheme;
    /// True when a desktop portal that publishes the colour scheme is installed (xdg-desktop-portal and its GTK
    /// backend). Without one a running Chromium never learns of a change, so launch carries an explicit hint.
    std::function<bool()> portalPresent;
    /// Defaults from the environment: XDG_DATA_HOME, TAOS_WEBAPP_EXEC.
    static Options fromEnvironment();
};

struct AddRequest {
    QString url;
    QString name;
    QString icon;              ///< "", "auto" or a file path
    QString source = QStringLiteral("custom");
    QString browser = QStringLiteral("auto");
    bool allowHttp = false;
};

class Engine {
public:
    explicit Engine(const Options &o = Options::fromEnvironment());
    ~Engine();
    Engine(const Engine &) = delete;
    Engine &operator=(const Engine &) = delete;

    QString root() const { return m_o.root; }
    QString applicationsDir() const { return m_o.applicationsDir; }

    QList<Instance> list() const;
    Result get(const QString &id, Instance *out = nullptr) const;
    /// `data.id` of the new instance.
    Result add(const AddRequest &req);
    /// Starts the browser detached. Records last used.
    Result launch(const QString &id);
    /// Copies the profile (sign-in comes along) into a new instance. Refused while the source runs.
    Result clone(const QString &id, const QString &newName = {});
    Result rename(const QString &id, const QString &name);
    Result remove(const QString &id, bool keepData = false, bool force = false);
    Result clearData(const QString &id, bool force = false);
    /// size on disk, last used, running, permissions when readable.
    Result info(const QString &id) const;
    /// Which browser engine is available; ok is false when none is installed.
    /// `withVersions` false skips running the browser for its version (the cheap check a window polls with).
    Result doctor(bool withVersions = true) const;
    /// Rewrites the launcher entries whose content differs (instances made before the window id was known keep working).
    void syncLaunchers() const;
    /// Sets the profile preference `browser.custom_chrome_frame` (Chromium's "Use system title bar and borders" OFF), so
    /// the window draws its own title bar with the minimise, full size and close buttons: the compositor draws none.
    /// Merges into `<profile>/<profile dir>/Preferences` without losing keys. Refused while the instance is running
    /// (Chromium rewrites the file on exit).
    Result ensureOwnTitleBar(const QString &id);

    bool isRunning(const QString &id) const;
    qint64 profileSize(const QString &id) const;
    QString instanceDir(const QString &id) const;
    QString profileDir(const QString &id) const;
    QString desktopFilePath(const QString &id) const;

    // helpers exposed for the faces and tests
    static bool validId(const QString &id);
    /// Normalises user text to a page URL: adds https:// to a bare host, requires https (http only with
    /// allowHttp), refuses every other scheme, credentials and an empty host. Empty result: see `error`.
    static QUrl normalizeUrl(const QString &input, bool allowHttp, QString *error);
    static QString monogramSvg(const QString &name);
    static QString hostLabel(const QUrl &url);

private:
    struct Private;
    Private *d;
    Options m_o;
    BrowserBackend *m_backend;
    bool saveIndex(const QList<Instance> &all, QString *error) const;
    QList<Instance> loadIndex() const;
    Result writeIcon(const QString &id, const QString &spec, const QUrl &page, const QString &monoName, QString *iconPath, QString *titleOut);
    Result writeDesktopFile(const Instance &inst) const;
    QString desktopText(const Instance &inst) const;
    Result writeManifest(const Instance &inst) const;
    QStringList readEnvFile(const QString &id) const;
};

/// Pure helpers (also used by tests).
struct IconCandidate { QUrl url; int size = 0; QString type; };
/// Finds icon candidates (largest first) and a title in a page and, if given, its web app manifest.
QList<IconCandidate> discoverIcons(const QUrl &page, const Fetcher &fetch, QString *title);

} // namespace TaosWebapp
