#pragma once
#include <QObject>

#include "taosqt/client.h"
#include "taosqt/error.h"
#include "taosqt/eventstream.h"
#include "taosqt/jsonmodel.h"

namespace Taos {

/// The pattern every list app needs: fetch a list, show it in a model, and
/// refetch it whenever the controller says it changed (the `/api/os/events`
/// stream carries only the kind, so "changed" means "ask again").
///
/// It refetches: at start, on a matching event, when the stream (re)connects
/// after a gap, and when credentials change. Overlapping refetches coalesce
/// (at most one in flight and one queued). The model is updated in place.
///
///     auto *q = new Taos::LiveQuery(client, u"notifications.list"_s, {},
///                                   {u"notification.added"_s, u"notification.updated"_s},
///                                   {u"id"_s, u"title"_s, u"message"_s, u"read"_s}, this);
///     q->start();      // QML: ListView { model: q.model }
class TAOSQT_EXPORT LiveQuery : public QObject
{
    Q_OBJECT
    Q_PROPERTY(Taos::JsonListModel *model READ model CONSTANT)
    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(bool live READ live NOTIFY liveChanged)
public:
    /// `eventKinds`: refetch only for these `kind`s (empty: any non-heartbeat
    /// event). `resultKey`: if the op returns an object, the key that holds the
    /// array (for example "items"); empty when the result is the array.
    LiveQuery(Client *client, const QString &op, const QJsonObject &args, const QStringList &eventKinds,
              const QStringList &roles, QObject *parent = nullptr, const QString &keyField = QStringLiteral("id"),
              const QString &resultKey = QString());
    ~LiveQuery() override;

    JsonListModel *model() const { return m_model; }
    bool loading() const { return m_loading; }
    QString errorText() const { return m_errorText; }
    /// True while the event stream is connected: the list is being kept fresh.
    /// False means the data may be stale (reconnecting) and says so for the UI.
    bool live() const { return m_live; }
    EventStream *stream() const { return m_stream; }

public Q_SLOTS:
    void start();
    void stop();
    void refresh();

Q_SIGNALS:
    void loadingChanged();
    void errorChanged();
    void liveChanged();
    void loaded();
    void failed(const Taos::Error &error);

private:
    void onStreamState(EventStream::State s);
    void setLoading(bool b);

    Client *m_client;
    QString m_op;
    QJsonObject m_args;
    QStringList m_kinds;
    QString m_resultKey;
    JsonListModel *m_model;
    EventStream *m_stream = nullptr;
    bool m_loading = false;
    bool m_pending = false;
    bool m_live = false;
    bool m_started = false;
    bool m_hadLive = false; // a gap in the event stream happened since the last fetch
    QString m_errorText;
};

} // namespace Taos
