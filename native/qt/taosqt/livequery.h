#pragma once
#include <QObject>
#include <QVariantMap>

#include "taosqt/client.h"
#include "taosqt/error.h"
#include "taosqt/eventstream.h"
#include "taosqt/jsonmodel.h"

namespace Taos {

/// The pattern every list app needs: fetch a list, show it in a model, and
/// refetch it whenever the controller says it changed (`/api/os/events` carries
/// only the kind, so "changed" means "ask again").
///
/// It uses the Client's ONE shared event stream (Client::sharedOsEvents) and
/// filters by `eventKinds`. It refetches: at start, on a matching event, on
/// every (re)connection of the stream (so an event sent between the initial
/// fetch and the stream going live is not lost), and when credentials change.
/// Overlapping refetches coalesce (one in flight, one queued). The model is
/// updated in place.
///
/// Usable straight from QML, no C++:
///
///     LiveQuery { id: q; op: "notifications.list"; running: true
///                 eventKinds: ["notification.added"]; roles: ["id", "title", "read"] }
///     ListView { model: q.model }
///
/// (Without a `client` it uses Taos::defaultClient().)
class TAOSQT_EXPORT LiveQuery : public QObject
{
    Q_OBJECT
    Q_PROPERTY(QString op READ op WRITE setOp NOTIFY configChanged)
    Q_PROPERTY(QVariantMap args READ args WRITE setArgs NOTIFY configChanged)
    Q_PROPERTY(QStringList eventKinds READ eventKinds WRITE setEventKinds NOTIFY configChanged)
    Q_PROPERTY(QStringList roles READ roles WRITE setRoles NOTIFY configChanged)
    Q_PROPERTY(QString keyField READ keyField WRITE setKeyField NOTIFY configChanged)
    Q_PROPERTY(QString resultKey READ resultKey WRITE setResultKey NOTIFY configChanged)
    Q_PROPERTY(bool running READ running WRITE setRunning NOTIFY runningChanged)
    Q_PROPERTY(Taos::JsonListModel *model READ model CONSTANT)
    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(bool live READ live NOTIFY liveChanged)
public:
    explicit LiveQuery(QObject *parent = nullptr);
    /// `eventKinds`: refetch only for these `kind`s (empty: any non-heartbeat
    /// event). `resultKey`: if the op returns an object, the key that holds the
    /// array (for example "items"); empty when the result is the array.
    LiveQuery(Client *client, const QString &op, const QJsonObject &args, const QStringList &eventKinds,
              const QStringList &roles, QObject *parent = nullptr, const QString &keyField = QStringLiteral("id"),
              const QString &resultKey = QString());
    ~LiveQuery() override;

    void setClient(Client *client);
    Client *client() const;
    QString op() const { return m_op; }
    void setOp(const QString &op);
    QVariantMap args() const { return m_args.toVariantMap(); }
    void setArgs(const QVariantMap &args);
    QStringList eventKinds() const { return m_kinds; }
    void setEventKinds(const QStringList &k);
    QStringList roles() const { return m_roles; }
    void setRoles(const QStringList &r);
    QString keyField() const { return m_keyField; }
    void setKeyField(const QString &k);
    QString resultKey() const { return m_resultKey; }
    void setResultKey(const QString &k) { m_resultKey = k; }

    JsonListModel *model() const { return m_model; }
    bool loading() const { return m_loading; }
    QString errorText() const { return m_errorText; }
    /// True while the event stream is connected: the list is being kept fresh.
    /// False means the data may be stale (reconnecting) and says so for the UI.
    bool live() const { return m_live; }
    bool running() const { return m_wantRunning; }
    void setRunning(bool on);

public Q_SLOTS:
    void start() { setRunning(true); }
    void stop() { setRunning(false); }
    void refresh();

Q_SIGNALS:
    void configChanged();
    void runningChanged();
    void loadingChanged();
    void errorChanged();
    void liveChanged();
    void loaded();
    void failed(const Taos::Error &error);

private:
    void applyRunning();
    void attach(Client *c);
    void detach();
    void setLoading(bool b);
    void setLive(bool b);

    QPointer<Client> m_client;
    QString m_op;
    QJsonObject m_args;
    QStringList m_kinds;
    QStringList m_roles;
    QString m_keyField = QStringLiteral("id");
    QString m_resultKey;
    JsonListModel *m_model;
    QPointer<EventStream> m_stream;
    bool m_wantRunning = false;
    bool m_attached = false;
    bool m_loading = false;
    bool m_pending = false;
    bool m_live = false;
    QString m_errorText;
};

} // namespace Taos
