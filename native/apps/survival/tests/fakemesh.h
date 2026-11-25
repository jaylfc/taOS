// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Stand-ins for the two engines, for the tests: a unix-socket server speaking the taos-mesh protocol and a loopback HTTP
// server speaking the taos-firmware API. Their answers are the real engines' JSON, captured by the engines' own tests
// (tests/fixtures/mesh and tests/fixtures/firmware). They record every request so a test can prove what was NOT sent:
// no send, no settings write, no flash, no radio connect.
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLocalServer>
#include <QLocalSocket>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTimer>
#include <QtTest>
#include <functional>

namespace FakeEngines {

inline QJsonValue fixture(const QString &dir, const QString &name, bool envelope = true)
{
    QFile f(dir + QLatin1Char('/') + name + QStringLiteral(".json"));
    if (!f.open(QIODevice::ReadOnly))
        qFatal("missing fixture %s", qPrintable(f.fileName()));
    const QJsonObject o = QJsonDocument::fromJson(f.readAll()).object();
    return envelope && o.contains(QStringLiteral("result")) ? o.value(QStringLiteral("result")) : QJsonValue(o);
}

class FakeMeshEngine : public QObject
{
    Q_OBJECT
public:
    explicit FakeMeshEngine(QString fixtures, QObject *parent = nullptr) : QObject(parent), m_fx(std::move(fixtures))
    {
        connect(&m_server, &QLocalServer::newConnection, this, [this] {
            while (QLocalSocket *c = m_server.nextPendingConnection()) {
                connect(c, &QLocalSocket::readyRead, this, [this, c] { read(c); });
                connect(c, &QLocalSocket::disconnected, this, [this, c] { m_subs.removeAll(c); m_conns.removeAll(c); m_buf.remove(c); c->deleteLater(); });
            }
        });
    }
    ~FakeMeshEngine() override
    {
        for (QLocalSocket *c : findChildren<QLocalSocket *>(QString(), Qt::FindChildrenRecursively)) {
            c->disconnect();
            delete c;
        }
        m_conns.clear();
        m_subs.clear();
    }
    bool listen(const QString &path)
    {
        m_path = path;
        QLocalServer::removeServer(path);
        return m_server.listen(path);
    }
    void close()
    {
        const auto conns = m_conns;
        for (QLocalSocket *s : conns)
            s->abort();
        m_server.close();
        QFile::remove(m_path);
    }
    QString path() const { return m_path; }
    bool receiveOnly = true;
    bool configReadable = false;
    bool refuseMessages = false;
    bool radiosConnect = true;
    QString savedDevice, connectError;
    // scripted radios for the switch tests: when `scripted` is set, app.radios / device.config / device.apply / radios.release
    // answer from these instead of the captured fixtures
    bool scripted = false, configWritable = false;
    QJsonArray radiosJson;
    QJsonObject cfgByRadio;                 // radio id -> the engine's device.config `values`
    QJsonArray serialDevices;               // what `discover` lists when scripted
    std::function<void(const QString &)> onConnect;
    std::function<void(const QString &)> onRelease;   // called with the port when radios.release arrives   // called with the port when radios.connect arrives
    QStringList released;
    QJsonArray textMessages, identitiesJson;   // when scripted and not empty, answer these (full engine records / app identities)
    int delayMs = 0;
    QList<QJsonObject> requests;
    int count(const QString &method) const { int n = 0; for (const auto &r : requests) n += r.value(QStringLiteral("method")).toString() == method; return n; }
    QJsonObject last(const QString &method) const { for (int i = requests.size() - 1; i >= 0; --i) if (requests[i].value(QStringLiteral("method")).toString() == method) return requests[i]; return {}; }
    /// None of the methods that write to a radio or open one. Only the ones named by `allowed` may have been called.
    QStringList forbiddenSeen(const QStringList &allowed = {}) const
    {
        QStringList bad;
        for (const auto &r : requests) {
            const QString m = r.value(QStringLiteral("method")).toString();
            if ((m == QLatin1String("app.send") || m == QLatin1String("send") || m == QLatin1String("device.apply") || m == QLatin1String("connect") || m == QLatin1String("radios.release")
                 || m == QLatin1String("radios.connect")) && !allowed.contains(m))
                bad << m;
        }
        return bad;
    }
    void pushRecord()
    {
        const QByteArray line = QJsonDocument(QJsonObject{{QStringLiteral("event"), QStringLiteral("record")}, {QStringLiteral("record"), QJsonObject{{QStringLiteral("id"), QStringLiteral("x")}}}}).toJson(QJsonDocument::Compact) + '\n';
        for (QLocalSocket *s : m_subs)
            s->write(line);
    }

private:
    void read(QLocalSocket *c)
    {
        if (!m_conns.contains(c))
            m_conns << c;
        m_buf[c] += c->readAll();
        int nl;
        while ((nl = m_buf[c].indexOf('\n')) >= 0) {
            const QJsonObject req = QJsonDocument::fromJson(m_buf[c].left(nl)).object();
            m_buf[c].remove(0, nl + 1);
            requests << req;
            if (req.value(QStringLiteral("method")).toString() == QLatin1String("subscribe")) {
                m_subs << c;
                c->write(QJsonDocument(QJsonObject{{QStringLiteral("id"), req.value(QStringLiteral("id"))}, {QStringLiteral("ok"), true}, {QStringLiteral("result"), QJsonObject{{QStringLiteral("subscribed"), true}}}}).toJson(QJsonDocument::Compact) + '\n');
                continue;
            }
            const QJsonObject resp = answer(req);
            auto send = [c, resp] { if (c->state() == QLocalSocket::ConnectedState) c->write(QJsonDocument(resp).toJson(QJsonDocument::Compact) + '\n'); };
            if (delayMs > 0)
                QTimer::singleShot(delayMs, c, send);
            else
                send();
        }
    }
    QJsonObject answer(const QJsonObject &req)
    {
        const QString m = req.value(QStringLiteral("method")).toString();
        const QJsonObject p = req.value(QStringLiteral("params")).toObject();
        auto ok = [&](const QJsonValue &v) { return QJsonObject{{QStringLiteral("id"), req.value(QStringLiteral("id"))}, {QStringLiteral("ok"), true}, {QStringLiteral("result"), v}}; };
        auto err = [&](const QString &code, const QString &msg) { return QJsonObject{{QStringLiteral("id"), req.value(QStringLiteral("id"))}, {QStringLiteral("ok"), false}, {QStringLiteral("error"), QJsonObject{{QStringLiteral("code"), code}, {QStringLiteral("message"), msg}}}}; };
        if (m == QLatin1String("health")) {
            QJsonObject h = fixture(m_fx, QStringLiteral("health")).toObject();
            h.insert(QStringLiteral("receive_only"), receiveOnly);
            h.insert(QStringLiteral("device_config"), configReadable);
            h.insert(QStringLiteral("device_config_write"), configWritable && !receiveOnly);
            h.insert(QStringLiteral("radios_connect"), radiosConnect);
            return ok(h);
        }
        if (m == QLatin1String("messages") && refuseMessages)
            return err(QStringLiteral("invalid"), QStringLiteral("bad filter: unknown field"));
        if (scripted && m == QLatin1String("messages") && !textMessages.isEmpty() && p.value(QStringLiteral("filter")).toObject().value(QStringLiteral("kind")).toString() != QLatin1String("position"))
            return ok(textMessages);
        if (scripted && m == QLatin1String("app.identities") && !identitiesJson.isEmpty())
            return ok(identitiesJson);
        if (m == QLatin1String("messages"))
            return ok(fixture(m_fx, p.value(QStringLiteral("filter")).toObject().value(QStringLiteral("kind")).toString() == QLatin1String("position") ? QStringLiteral("messages.position") : QStringLiteral("messages.text")));
        if (m == QLatin1String("contacts.create"))
            return ok(QJsonObject{{QStringLiteral("contact"), QStringLiteral("c9")}});
        if (scripted && m == QLatin1String("app.radios"))
            return ok(radiosJson);
        if (scripted && m == QLatin1String("device.config")) {
            const QString id = p.value(QStringLiteral("radio")).toString();
            if (!cfgByRadio.contains(id))
                return err(QStringLiteral("no_radio"), QStringLiteral("no radio ") + id);
            return ok(QJsonObject{{QStringLiteral("radio"), id}, {QStringLiteral("state"), QStringLiteral("ok")}, {QStringLiteral("note"), QString()}, {QStringLiteral("values"), cfgByRadio.value(id)}});
        }
        if (scripted && m == QLatin1String("device.apply"))
            return ok(QJsonObject{{QStringLiteral("applied"), QJsonArray::fromStringList(p.value(QStringLiteral("changes")).toObject().keys())}});
        if (m == QLatin1String("radios.release")) {
            released << p.value(QStringLiteral("device")).toString();
            if (onRelease)
                onRelease(released.last());
            if (scripted)
                radiosJson = QJsonArray();
            return ok(QJsonObject{{QStringLiteral("released"), true}, {QStringLiteral("was_open"), true}});
        }
        if (m == QLatin1String("device.config")) {
            if (!configReadable)
                return err(QStringLiteral("unknown_method"), QStringLiteral("no method device.config"));
            return ok(QJsonObject{{QStringLiteral("network"), QStringLiteral("meshtastic")}, {QStringLiteral("owner"), QJsonObject{{QStringLiteral("long_name"), QStringLiteral("Fixture radio")}, {QStringLiteral("short_name"), QStringLiteral("FX")}}},
                                  {QStringLiteral("channels"), QJsonArray{QJsonObject{{QStringLiteral("index"), 0}, {QStringLiteral("name"), QStringLiteral("Primary")}, {QStringLiteral("role"), QStringLiteral("PRIMARY")},
                                                                         {QStringLiteral("psk_b64"), QStringLiteral("AQ==")}, {QStringLiteral("share_url"), QStringLiteral("https://example.invalid/e/#abc")}}}}});
        }
        if (scripted && m == QLatin1String("discover"))
            return ok(QJsonObject{{QStringLiteral("serial"), serialDevices}, {QStringLiteral("ble"), QJsonObject{{QStringLiteral("state"), QStringLiteral("devices")}, {QStringLiteral("devices"), QJsonArray()}}}});
        if (m == QLatin1String("discover"))
            return ok(QJsonObject{{QStringLiteral("serial"), QJsonArray{QJsonObject{{QStringLiteral("path"), QStringLiteral("/dev/ttyACM0")}, {QStringLiteral("vid"), 0x303a}, {QStringLiteral("pid"), 0x1001},
                                       {QStringLiteral("product"), QStringLiteral("Fixture board")}, {QStringLiteral("manufacturer"), QStringLiteral("Fixture")}, {QStringLiteral("hint"), QStringLiteral("Espressif native USB")}}}},
                                  {QStringLiteral("ble"), QJsonObject{{QStringLiteral("state"), QStringLiteral("devices")}, {QStringLiteral("devices"), QJsonArray{QJsonObject{{QStringLiteral("address"), QStringLiteral("AA:BB:CC:DD:EE:FF")}, {QStringLiteral("name"), QStringLiteral("Fixture BLE")}, {QStringLiteral("network"), QStringLiteral("meshtastic")}}}}}}});
        if (m == QLatin1String("radios.saved")) {
            QJsonArray saved;
            if (!savedDevice.isEmpty())
                saved.append(QJsonObject{{QStringLiteral("device"), savedDevice}});
            return ok(saved);
        }
        if (m == QLatin1String("radios.connect")) {
            if (!connectError.isEmpty())
                return err(QStringLiteral("connect_failed"), connectError);
            savedDevice = p.value(QStringLiteral("device")).toString();
            if (onConnect)
                onConnect(savedDevice);
            return ok(QJsonObject{{QStringLiteral("radio"), QStringLiteral("!fixture")}, {QStringLiteral("network"), QStringLiteral("meshtastic")}});
        }
        if (m == QLatin1String("radios.disconnect")) {
            savedDevice.clear();
            return ok(QJsonObject{{QStringLiteral("forgotten"), true}});
        }
        if (m == QLatin1String("app.send"))
            return ok(QJsonObject{{QStringLiteral("accepted"), true}, {QStringLiteral("messageId"), QStringLiteral("m1")}, {QStringLiteral("error"), QString()}});
        for (const char *name : {"app.radios", "app.identities", "last_positions", "identities.list"})
            if (m == QLatin1String(name))
                return ok(fixture(m_fx, m));
        if (m == QLatin1String("identities.assign") || m == QLatin1String("contacts.told") || m == QLatin1String("link"))
            return ok(QJsonObject());
        return err(QStringLiteral("unknown_method"), QStringLiteral("no method ") + m);
    }
    QString m_fx, m_path;
    QList<QLocalSocket *> m_conns, m_subs;
    QHash<QLocalSocket *, QByteArray> m_buf;
    QLocalServer m_server;   // last: its connections are destroyed before the lists their slots use
};

/// A loopback HTTP server that answers like taos-firmware's API, from the engine's own captured replies. It cannot touch
/// a device. Requests are recorded (method, path, body).
class FakeFirmware : public QObject
{
    Q_OBJECT
public:
    struct Req { QString method, path; QJsonObject body; QByteArray auth; };
    FakeFirmware(QString fixtures, QString dataDir, QObject *parent = nullptr, quint16 fixedPort = 0) : QObject(parent), m_fx(std::move(fixtures))
    {
        QDir().mkpath(dataDir);
        QFile t(dataDir + QStringLiteral("/api-token"));
        if (t.open(QIODevice::WriteOnly)) t.write("test-token");
        connect(&m_srv, &QTcpServer::newConnection, this, [this] {
            while (QTcpSocket *s = m_srv.nextPendingConnection())
                connect(s, &QTcpSocket::readyRead, this, [this, s] { read(s); });
        });
        m_srv.listen(QHostAddress::LocalHost, fixedPort);
        // the owner socket: mints a nonce per request, like the engine's 0600 socket (which also checks the peer uid)
        QLocalServer::removeServer(dataDir + QStringLiteral("/owner.sock"));
        m_owner.listen(dataDir + QStringLiteral("/owner.sock"));
        connect(&m_owner, &QLocalServer::newConnection, this, [this] {
            while (QLocalSocket *c = m_owner.nextPendingConnection())
                connect(c, &QLocalSocket::readyRead, c, [this, c] {
                    if (!c->canReadLine())
                        return;
                    const QJsonObject o = QJsonDocument::fromJson(c->readLine()).object();
                    const QString n = QStringLiteral("nonce-%1").arg(mints.size() + 1);
                    mints.append({o.value(QStringLiteral("action")).toString(), o.value(QStringLiteral("subject")).toString(), n});
                    c->write(QJsonDocument(QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("nonce"), n}, {QStringLiteral("expires_in"), 300}}).toJson(QJsonDocument::Compact) + '\n');
                    c->disconnectFromServer();
                });
        });
    }
    int port() const { return m_srv.serverPort(); }
    struct Mint { QString action, subject, nonce; };
    QList<Mint> mints;
    QStringList used;
    QList<Req> requests;
    QString mode = QStringLiteral("ready");   // ready | pending
    int runDelayMs = 0;                       // how long /v1/flash/run takes to answer (pictures of a flash in progress)
    QString runName = QStringLiteral("Heltec V3");   // the board name /v1/flash/run accepts
    int count(const QString &method, const QString &path) const { int n = 0; for (const auto &r : requests) n += r.method == method && r.path == path; return n; }
    Req last(const QString &path) const { for (int i = requests.size() - 1; i >= 0; --i) if (requests[i].path == path) return requests[i]; return {}; }

private:
    void read(QTcpSocket *s)
    {
        m_buf[s] += s->readAll();
        const int he = m_buf[s].indexOf("\r\n\r\n");
        if (he < 0)
            return;
        const QByteArray head = m_buf[s].left(he);
        int cl = 0;
        for (const QByteArray &l : head.split('\n'))
            if (l.toLower().startsWith("content-length:"))
                cl = l.mid(15).trimmed().toInt();
        if (m_buf[s].size() < he + 4 + cl)
            return;
        const QByteArray body = m_buf[s].mid(he + 4, cl);
        m_buf.remove(s);
        const QList<QByteArray> first = head.split('\n').first().trimmed().split(' ');
        Req r;
        r.method = QString::fromLatin1(first.value(0));
        r.path = QString::fromLatin1(first.value(1)).section(QLatin1Char('?'), 0, 0);
        r.body = QJsonDocument::fromJson(body).object();
        for (const QByteArray &l : head.split('\n'))
            if (l.toLower().startsWith("authorization:"))
                r.auth = l.mid(14).trimmed();
        requests << r;
        QJsonValue out;
        int code = 200;
        // the engine's rule: accepting a licence and running a flash need a nonce minted for exactly that action and subject, used once
        auto nonceOk = [this](const Req &q, const QString &action, const QString &subject) {
            const QString n = q.body.value(QStringLiteral("owner_nonce")).toString();
            if (n.isEmpty() || used.contains(n))
                return false;
            used << n;
            for (const Mint &m : mints)
                if (m.nonce == n)
                    return m.action == action && m.subject == subject;
            return false;
        };
        const bool needsNonce = r.path == QLatin1String("/v1/licences/accept") || r.path == QLatin1String("/v1/flash/run");
        const QString subject = r.path == QLatin1String("/v1/licences/accept") ? r.body.value(QStringLiteral("network")).toString()
                                                                                : r.body.value(QStringLiteral("device")).toString() + QLatin1Char('|') + r.body.value(QStringLiteral("entry")).toString();
        if (r.auth == "Bearer test-token" && needsNonce && !nonceOk(r, r.path == QLatin1String("/v1/licences/accept") ? QStringLiteral("licence.accept") : QStringLiteral("flash.run"), subject)) {
            out = QJsonObject{{QStringLiteral("ok"), false}, {QStringLiteral("error"), QStringLiteral("owner nonce required")}};
            code = 403;
        } else if (r.auth != "Bearer test-token") { out = QJsonObject{{QStringLiteral("ok"), false}, {QStringLiteral("error"), QStringLiteral("unauthorized")}}; code = 401; }
        else out = route(r);
        const QByteArray payload = QJsonDocument(out.toObject()).toJson(QJsonDocument::Compact);
        const QByteArray reply = "HTTP/1.1 " + QByteArray::number(code) + " OK\r\nContent-Type: application/json\r\nContent-Length: " + QByteArray::number(payload.size()) + "\r\nConnection: close\r\n\r\n" + payload;
        auto answer = [s, reply] { if (s->state() == QAbstractSocket::ConnectedState) { s->write(reply); s->disconnectFromHost(); } };
        if (r.path == QLatin1String("/v1/flash/run") && runDelayMs > 0)
            QTimer::singleShot(runDelayMs, s, answer);
        else
            answer();
    }
    QJsonValue route(const Req &r)
    {
        auto fx = [this](const QString &n) { return fixture(m_fx, n, false); };
        if (r.path == QLatin1String("/v1/boards")) return fx(QStringLiteral("boards"));
        if (r.path == QLatin1String("/v1/library")) return mode == QLatin1String("pending") ? QJsonValue(QJsonObject{{QStringLiteral("ok"), true}, {QStringLiteral("entries"), QJsonArray()}}) : fx(QStringLiteral("library"));
        if (r.path == QLatin1String("/v1/licences")) return fx(mode == QLatin1String("pending") ? QStringLiteral("licences_pending") : QStringLiteral("licences_accepted"));
        if (r.path == QLatin1String("/v1/licences/accept")) return QJsonObject{{QStringLiteral("ok"), true}};
        if (r.path == QLatin1String("/v1/library/refresh")) return fx(QStringLiteral("refresh_ok"));
        if (r.path == QLatin1String("/v1/devices")) return fx(QStringLiteral("devices"));
        if (r.path.endsWith(QLatin1String("/firmware"))) return fx(QStringLiteral("device_firmware"));
        if (r.path == QLatin1String("/v1/flash/plan")) return fx(QStringLiteral("plan"));
        if (r.path == QLatin1String("/v1/flash/run") && r.body.contains(QStringLiteral("actor")) && r.body.value(QStringLiteral("actor")).toString() != QLatin1String("person")
            && !r.body.value(QStringLiteral("actor")).toString().startsWith(QLatin1String("agent:")))
            return QJsonObject{{QStringLiteral("ok"), false}, {QStringLiteral("stage"), QStringLiteral("refused")}, {QStringLiteral("code"), QStringLiteral("consent_required")}, {QStringLiteral("message"), QStringLiteral("actor must be person or agent:<id>")}};
        if (r.path == QLatin1String("/v1/flash/run")) {
            if (r.body.value(QStringLiteral("confirm")).toString() != runName)
                return fx(QStringLiteral("run_unconfirmed"));
            if (!r.body.value(QStringLiteral("ack_unsigned")).toBool())
                return fx(QStringLiteral("run_unacknowledged"));
            return fx(QStringLiteral("run_done"));
        }
        return QJsonObject{{QStringLiteral("ok"), false}, {QStringLiteral("error"), QStringLiteral("no route")}};
    }
    QString m_fx;
    QTcpServer m_srv;
    QLocalServer m_owner;
    QHash<QTcpSocket *, QByteArray> m_buf;
};

} // namespace FakeEngines
