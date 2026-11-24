// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The engine of the native Store: the catalogue (controller entries merged with the built-in web apps), the
// install state machine, models, themes and installed items, and the web apps engine. A plain QObject with no UI.
// The controller routes are those of the web Store (see native/apps/PARITY-store.md for file:line).
#include <QJsonObject>
#include <QPointer>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>
#include <memory>

#include "storecatalog.h"
#include "taosqt/client.h"
#include "taosqt/session.h"
#include "webappsservice.h"

class ThemeManager;
class Theme;

namespace Taos::Store {

/// Wraps the shell's ThemeManager: the installed themes and Apply. Same engine and files as Settings > Appearance.
class ThemesService : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList themes READ themes NOTIFY changed)
    Q_PROPERTY(QString mode READ mode NOTIFY changed)
public:
    struct Paths {
        QString appearancePath, mirrorDir, builtinDir;
        QStringList themeDirs;
        bool watch = true;
        bool useDefaults = true;
    };
    explicit ThemesService(QObject *parent = nullptr);
    explicit ThemesService(const Paths &p, QObject *parent = nullptr);
    ~ThemesService() override;
    QVariantList themes() const;
    QString mode() const;
    Q_INVOKABLE bool apply(const QString &id);
    /// "built-in" or "yours"
    Q_INVOKABLE QString originOf(const QString &id) const;
Q_SIGNALS:
    void changed();
private:
    std::unique_ptr<Theme> m_theme;
    std::unique_ptr<ThemeManager> m_mgr;
};

class StoreEngine : public QObject {
    Q_OBJECT
    Q_PROPERTY(Taos::Session *session READ session WRITE setSession NOTIFY sessionChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(bool loaded READ loaded NOTIFY stateChanged)
    Q_PROPERTY(bool loading READ loading NOTIFY stateChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY stateChanged)
    /// "", "forbidden", "unauthenticated", "network" or "other".
    Q_PROPERTY(QString errorKind READ errorKind NOTIFY stateChanged)
    Q_PROPERTY(bool isAdmin READ isAdmin NOTIFY stateChanged)
    /// Bumped on every change a list or a detail page shows, so bindings that call list(), app() re-run.
    Q_PROPERTY(int revision READ revision NOTIFY revisionChanged)
    Q_PROPERTY(QObject *webapps READ webappsObject CONSTANT)
    Q_PROPERTY(QObject *themes READ themesObject CONSTANT)
    Q_PROPERTY(QString profileId READ profileId NOTIFY stateChanged)
    Q_PROPERTY(QVariantList sections READ sections CONSTANT)
public:
    explicit StoreEngine(QObject *parent = nullptr);
    ~StoreEngine() override;
    /// For tests: a client without a session, a private web apps engine and private theme folders.
    void setClientForTest(Taos::Client *c) { m_client = c; }
    void setWebappOptions(const TaosWebapp::Options &o);
    void setThemePaths(const ThemesService::Paths &p);
    void setBuiltinWebapps(const QJsonArray &apps);
    /// Loads catalog/webapps.json (embedded at :/my.taos.store/webapps.json) when nothing was set.
    void loadBuiltinWebapps();

    Taos::Session *session() const { return m_session; }
    void setSession(Taos::Session *s);
    bool running() const { return m_running; }
    void setRunning(bool on);
    bool loaded() const { return m_loaded; }
    bool loading() const { return m_loading > 0; }
    QString errorText() const { return m_errorText; }
    QString errorKind() const { return m_errorKind; }
    bool isAdmin() const;
    int revision() const { return m_revision; }
    QString profileId() const { return m_profile; }
    QObject *webappsObject() { return m_web; }
    QObject *themesObject() { return m_themes.get(); }
    WebAppsService *web() { return m_web; }
    ThemesService *themesService() { return m_themes.get(); }
    QVariantList sections() const;
    Catalog &catalog() { return m_cat; }

    // ---- catalogue
    Q_INVOKABLE void refresh();
    /// Entries of a section for a search text and a chip. "discover" returns groups, see discover().
    Q_INVOKABLE QVariantList list(const QString &section, const QString &query = {}, const QString &chip = {}) const;
    Q_INVOKABLE QVariantList chips(const QString &section) const { return m_cat.chips(section); }
    Q_INVOKABLE QVariantList discover() const { return m_cat.discover(); }
    Q_INVOKABLE QVariantList search(const QString &query) const { return m_cat.search(query); }
    Q_INVOKABLE int count(const QString &section) const;
    /// One entry, with what the detail page needs (permissions rows, model variants, instances).
    Q_INVOKABLE QVariantMap app(const QString &id) const;
    Q_INVOKABLE void loadDetail(const QString &id);
    Q_INVOKABLE QVariantList asks(const QString &id) const;
    /// Installed items of every kind: [{id,name,kind,kindLabel,subtitle,letter,source}].
    Q_INVOKABLE QVariantList installedRows() const;

    // ---- install state machine (container apps, agents, models, plugins)
    /// {state, text, percent, detail, consent, consentText, error}; state: idle consent installing removing installed failed
    Q_INVOKABLE QVariantMap installState(const QString &id) const;
    Q_INVOKABLE void install(const QString &id, const QString &variantId = {});
    Q_INVOKABLE void acceptConsent(const QString &id);
    Q_INVOKABLE void cancelConsent(const QString &id);
    Q_INVOKABLE void remove(const QString &id);
    Q_INVOKABLE void dismiss(const QString &id);
    /// Open an installed app: its window or its web address. False (and a toast) when there is nothing to open.
    Q_INVOKABLE bool open(const QString &id);

    // ---- web apps
    /// Install a catalogue web app: adds an instance (named after the entry; a second one gets "(2)").
    Q_INVOKABLE void installWebapp(const QString &storeId);
    Q_INVOKABLE void addWebsite(const QString &url, const QString &name, bool autoIcon = true);

Q_SIGNALS:
    void sessionChanged();
    void runningChanged();
    void stateChanged();
    void revisionChanged();
    void toast(const QString &text);
    /// An install/remove reached an end (ok or not).
    void installFinished(const QString &id, bool ok, const QString &text);

private:
    struct Inst {
        QString state = QStringLiteral("idle");
        QString text, detail, consent, consentText, variant, error;
        double percent = -1;
        bool dockerOk = false, licenseOk = false;
        QPointer<Taos::Reply> reply;
    };
    Taos::Client *client() const;
    void bump();
    void fail(const QString &what, const Taos::Error &e);
    void fetchInstalled();
    void fetchModels();
    void fetchHardware();
    void postInstall(const QString &id);
    void pollProgress();
    void finishInstall(const QString &id, bool ok, const QString &text);
    QString refusal(const Taos::Error &e, const QString &verb) const;
    void note(const QString &id);

    QPointer<Taos::Client> m_client;
    QPointer<Taos::Session> m_session;
    Catalog m_cat;
    WebAppsService *m_web;
    std::unique_ptr<ThemesService> m_themes;
    QHash<QString, Inst> m_inst;
    QSet<QString> m_detailWanted;
    QHash<QString, QVariantMap> m_detail;          ///< GET /api/store/app/{id}
    QHash<QString, QVariantMap> m_runtime;         ///< installed-v2 rows by app id
    QHash<QString, QJsonObject> m_modelRows;       ///< /api/models rows by id
    QTimer m_poll;
    QString m_errorText, m_errorKind, m_profile;
    bool m_running = false, m_loaded = false, m_haveBuiltin = false;
    int m_loading = 0;
    int m_revision = 0;
};

void registerStoreQmlTypes();

} // namespace Taos::Store
