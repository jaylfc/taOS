#pragma once
// ActivityEngine: the service behind the Activity app. It polls the controller
// (nothing else), keeps the last good answer of each call, derives the merged
// views the web app derives and a short in-memory graph history. It owns no
// data the web app would not see: everything shown comes from the controller
// API through the foundation's Client. The QML is a face over this object.
#include <QDateTime>
#include <QJsonObject>
#include <QObject>
#include <QPointer>
#include <QTimer>
#include <QVariantList>
#include <QVariantMap>

#include "taosqt/client.h"

namespace Taos::Activity {

class ActivityEngine : public QObject
{
    Q_OBJECT
    Q_PROPERTY(Taos::Client *client READ client WRITE setClient NOTIFY clientChanged)
    /// True while the window is visible: polling runs only then.
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
    Q_PROPERTY(int phase READ phase NOTIFY phaseChanged)           ///< Phase
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(bool refreshing READ refreshing NOTIFY refreshingChanged)
    Q_PROPERTY(double now READ now NOTIFY dataChanged)             ///< epoch seconds of the last tick
    Q_PROPERTY(QVariantMap activity READ activity NOTIFY dataChanged)
    Q_PROPERTY(QVariantMap hardware READ hardware NOTIFY dataChanged)
    Q_PROPERTY(QVariantList gpus READ gpus NOTIFY dataChanged)
    Q_PROPERTY(QVariantList loadedModels READ loadedModels NOTIFY dataChanged)
    Q_PROPERTY(bool hasScheduler READ hasScheduler NOTIFY dataChanged)
    Q_PROPERTY(QVariantMap scheduler READ scheduler NOTIFY dataChanged)
    Q_PROPERTY(QVariantList tasks READ tasks NOTIFY dataChanged)
    Q_PROPERTY(QVariantList workers READ workers NOTIFY dataChanged)
    Q_PROPERTY(QVariantMap history READ history NOTIFY dataChanged)
    Q_PROPERTY(int restartPhase READ restartPhase NOTIFY restartChanged)  ///< RestartPhase
    Q_PROPERTY(QVariantMap restartResult READ restartResult NOTIFY restartChanged)
    Q_PROPERTY(QString restartError READ restartError NOTIFY restartChanged)
    Q_PROPERTY(int pollIntervalMs READ pollIntervalMs WRITE setPollIntervalMs NOTIFY intervalsChanged)
    Q_PROPERTY(int workersIntervalMs READ workersIntervalMs WRITE setWorkersIntervalMs NOTIFY intervalsChanged)
public:
    enum Phase { Loading, Ready, Unavailable };
    Q_ENUM(Phase)
    enum RestartPhase { RestartIdle, RestartConfirm, RestartRunning, RestartDone, RestartError };
    Q_ENUM(RestartPhase)

    static constexpr int kHistory = 60;     ///< graph samples kept (two minutes at 2 s)
    static constexpr int kTaskLimit = 8;

    explicit ActivityEngine(QObject *parent = nullptr);
    ~ActivityEngine() override;

    Client *client() const;
    void setClient(Client *c);
    bool active() const { return m_active; }
    void setActive(bool on);
    int phase() const { return m_phase; }
    QString errorText() const { return m_error; }
    bool refreshing() const { return m_inFlight > 0 || m_workersInFlight; }
    double now() const { return m_now; }
    QVariantMap activity() const { return m_activity; }
    QVariantMap hardware() const { return m_activity.value(QStringLiteral("hardware")).toMap(); }
    QVariantList gpus() const { return m_gpus; }
    QVariantList loadedModels() const { return m_loaded; }
    bool hasScheduler() const { return m_hasScheduler; }
    QVariantMap scheduler() const { return m_scheduler; }
    QVariantList tasks() const { return m_tasks; }
    QVariantList workers() const { return m_workersView; }
    QVariantMap history() const { return m_history; }
    int restartPhase() const { return m_restartPhase; }
    QVariantMap restartResult() const { return m_restartResult; }
    QString restartError() const { return m_restartError; }
    int pollIntervalMs() const { return m_poll.interval(); }
    void setPollIntervalMs(int ms) { m_poll.setInterval(ms); Q_EMIT intervalsChanged(); }
    int workersIntervalMs() const { return m_workersTimer.interval(); }
    void setWorkersIntervalMs(int ms) { m_workersTimer.setInterval(ms); Q_EMIT intervalsChanged(); }

    /// The text of the restart confirmation: exactly what the route restarts.
    Q_INVOKABLE static QString restartSummary();

public Q_SLOTS:
    /// Manual refresh and refresh on window focus: the 2 s batch and the workers.
    void refresh();
    void refreshWorkers();
    /// The Restart AI services action: confirm -> running -> done | error.
    void askRestart();
    void confirmRestart();
    void dismissRestart();

Q_SIGNALS:
    void clientChanged();
    void activeChanged();
    void phaseChanged();
    void errorChanged();
    void refreshingChanged();
    void dataChanged();
    void restartChanged();
    void intervalsChanged();
    /// One batch finished (for tests and for the busy indicator).
    void batchFinished(bool activityOk);
    void workersFinished(bool ok);

private:
    void fetchBatch();
    void finishBatch();
    void pushHistory(const QString &key, double v);
    void rebuildWorkers();
    void setPhase(Phase p);
    void setError(const QString &e);
    void setRestart(RestartPhase p);

    QPointer<Client> m_client;
    QTimer m_poll;
    QTimer m_workersTimer;
    bool m_active = false;
    Phase m_phase = Loading;
    QString m_error;
    int m_inFlight = 0;
    bool m_again = false;
    bool m_activityOk = false;
    bool m_workersInFlight = false;
    double m_now = 0;
    QVariantMap m_activity;
    QVariantList m_gpus;
    QVariantList m_localLoaded;
    QVariantList m_loaded;
    bool m_hasScheduler = false;
    QVariantMap m_scheduler;
    QVariantList m_tasks;
    QVariantList m_workersRaw;
    QVariantList m_workersView;
    QVariantMap m_history;
    RestartPhase m_restartPhase = RestartIdle;
    QVariantMap m_restartResult;
    QString m_restartError;
    // Stage of the batch being assembled (applied together when all four answered).
    struct Pending {
        bool ok[4] = {false, false, false, false};
        QJsonValue v[4];
        Error e[4];
    } m_pending;
};

} // namespace Taos::Activity
