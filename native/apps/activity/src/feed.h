// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// ModelActivityFeed: the model-activity timeline. History is seeded from
// GET /api/activity/models, then live frames from the SSE stream
// /api/activity/models/stream are merged in by `seq` (never replaced, so frames
// that land while the history request is in flight are kept). Both routes
// take the session cookie only; with the host local token they answer 401 and
// the feed says so ("Sign in to see model activity").
#include <QMap>
#include <QJsonObject>
#include <QObject>
#include <QPointer>
#include <QSet>
#include <QVariantList>
#include <QVariantMap>

#include "taosqt/client.h"
#include "taosqt/eventstream.h"
#include "taosqt/jsonmodel.h"

namespace Taos::Activity {

class ModelActivityFeed : public QObject
{
    Q_OBJECT
    Q_PROPERTY(Taos::Client *client READ client WRITE setClient NOTIFY clientChanged)
    /// Window visible: the stream is open only then.
    Q_PROPERTY(bool active READ active WRITE setActive NOTIFY activeChanged)
    // The three filter values are the only state the app owns (view state).
    Q_PROPERTY(QString worker READ worker WRITE setWorker NOTIFY filtersChanged)
    Q_PROPERTY(QString modelFilter READ modelFilter WRITE setModelFilter NOTIFY filtersChanged)
    Q_PROPERTY(QString eventType READ eventType WRITE setEventType NOTIFY filtersChanged)
    /// Newest first, at most 200 rows, updated in place by `seq` (roles: seq, ts, event, model, worker, ...).
    Q_PROPERTY(Taos::JsonListModel *model READ eventModel CONSTANT)
    Q_PROPERTY(int count READ count NOTIFY eventsChanged)
    Q_PROPERTY(QStringList workerOptions READ workerOptions NOTIFY optionsChanged)
    Q_PROPERTY(QStringList modelOptions READ modelOptions NOTIFY optionsChanged)
    Q_PROPERTY(QStringList eventTypes READ eventTypes NOTIFY optionsChanged)
    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool live READ live NOTIFY liveChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(bool signInNeeded READ signInNeeded NOTIFY errorChanged)
public:
    static constexpr int kMaxEvents = 200; // MAX_MODEL_ACTIVITY_EVENTS

    explicit ModelActivityFeed(QObject *parent = nullptr);
    ~ModelActivityFeed() override;

    Client *client() const;
    void setClient(Client *c);
    bool active() const { return m_active; }
    void setActive(bool on);
    QString worker() const { return m_worker; }
    QString modelFilter() const { return m_modelFilter; }
    QString eventType() const { return m_event; }
    void setWorker(const QString &v);
    void setModelFilter(const QString &v);
    void setEventType(const QString &v);
    JsonListModel *eventModel() const { return m_model; }
    int count() const { return m_bySeq.size(); }
    QStringList workerOptions() const { auto l = m_workers.values(); l.sort(); return l; }
    QStringList modelOptions() const { auto l = m_models.values(); l.sort(); return l; }
    QStringList eventTypes() const { return m_types; }
    bool loading() const { return m_loading; }
    bool live() const { return m_live; }
    QString errorText() const { return m_error; }
    bool signInNeeded() const { return m_signIn; }

public Q_SLOTS:
    void reload();

Q_SIGNALS:
    void clientChanged();
    void activeChanged();
    void filtersChanged();
    void eventsChanged();
    void optionsChanged();
    void loadingChanged();
    void liveChanged();
    void errorChanged();

private:
    void restart(bool clear); // fetch history, open the stream; `clear` empties the list first
    void closeStream();
    void openStream();
    void absorb(const QJsonArray &events);
    void publish();
    void setLive(bool on);
    void setError(const QString &text, bool signIn);
    QJsonObject query(bool stream) const;

    QPointer<Client> m_client;
    QPointer<EventStream> m_stream;
    QPointer<Reply> m_history;
    bool m_active = false;
    QString m_worker, m_modelFilter, m_event;
    QMap<qint64, QJsonObject> m_bySeq;   // newest = last key
    JsonListModel *m_model;
    QSet<QString> m_workers, m_models;
    QStringList m_types;
    bool m_loading = false;
    bool m_live = false;
    QString m_error;
    bool m_signIn = false;
    quint64 m_generation = 0;
};

} // namespace Taos::Activity
