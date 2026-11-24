#pragma once
// Test harness: the fake controller (extended, fake_apps_controller.py) or a real
// one (TAOS_REAL_URL), a signed-in Session, and a QML app loaded in an engine.
#include <QCoreApplication>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>
#include <QQmlComponent>
#include <QQmlEngine>
#include <QQuickItem>
#include <QQuickWindow>
#include <QSignalSpy>
#include <QTcpSocket>
#include <QtTest>
#include <functional>
#include <memory>

#include "taosqt/appcontext.h"
#include "taosqt/qmlregister.h"
#include "taosqt/session.h"

class FakeHost
{
public:
    FakeHost()
    {
        m_proc.setProgram(QStringLiteral("python3"));
        m_proc.setArguments({QStringLiteral(FAKE_APPS_PY)});
        m_proc.start();
        if (m_proc.waitForReadyRead(10000)) {
            const QByteArray line = m_proc.readLine();
            if (line.startsWith("LISTENING "))
                m_port = line.mid(10).trimmed().toUShort();
        }
    }
    ~FakeHost()
    {
        m_proc.kill();
        m_proc.waitForFinished(3000);
    }
    bool ok() const { return m_port != 0; }
    QUrl url() const { return QUrl(QStringLiteral("http://127.0.0.1:%1").arg(m_port)); }
    void config(const QJsonObject &o) { request("POST", "/__fake/config", QJsonDocument(o).toJson(QJsonDocument::Compact)); }
    /// Back to the seeded state (notifications, requests, decisions, projects), no recorded calls.
    /// Sessions are kept (a reset would sign the app out) and injected failures are cleared.
    void reseed()
    {
        config({{QStringLiteral("fail"), QJsonValue()}});
        config({{QStringLiteral("seed"), true}});
    }
    QJsonArray calls() { return QJsonDocument::fromJson(request("GET", "/__fake/calls", {})).array(); }
    /// The recorded POSTs to `path`.
    QJsonArray callsTo(const QString &path)
    {
        QJsonArray out;
        for (const QJsonValue &v : calls())
            if (v.toObject().value(QStringLiteral("path")).toString() == path)
                out.append(v);
        return out;
    }

private:
    QByteArray request(const char *method, const char *path, const QByteArray &body)
    {
        QTcpSocket s;
        s.connectToHost(QStringLiteral("127.0.0.1"), m_port);
        if (!s.waitForConnected(3000))
            return {};
        s.write(QByteArray(method) + " " + path + " HTTP/1.1\r\nHost: x\r\nConnection: close\r\nContent-Length: "
                + QByteArray::number(body.size()) + "\r\n\r\n" + body);
        s.waitForBytesWritten(3000);
        QByteArray all;
        while (s.waitForReadyRead(3000))
            all += s.readAll();
        all += s.readAll();
        return all.mid(all.indexOf("\r\n\r\n") + 4);
    }
    QProcess m_proc;
    quint16 m_port = 0;
};

inline Taos::ClientOptions hostOptions(const QUrl &url)
{
    Taos::ClientOptions o;
    o.baseUrl = url;
    o.requestTimeoutMs = 8000;
    o.connectTimeoutMs = 2000;
    o.streamBackoffBaseMs = 50;
    o.streamBackoffCapMs = 200;
    return o;
}

/// Spin the event loop until `cond()` or `ms` elapse.
inline bool waitFor(const std::function<bool()> &cond, int ms = 8000)
{
    QElapsedTimer t;
    t.start();
    while (!cond() && t.elapsed() < ms)
        QTest::qWait(15);
    return cond();
}

inline QQuickItem *findItem(QQuickItem *root, const std::function<bool(QQuickItem *)> &pred)
{
    if (!root)
        return nullptr;
    if (pred(root))
        return root;
    for (QQuickItem *c : root->childItems())
        if (QQuickItem *r = findItem(c, pred))
            return r;
    return nullptr;
}

inline QList<QQuickItem *> findItems(QQuickItem *root, const std::function<bool(QQuickItem *)> &pred)
{
    QList<QQuickItem *> out;
    std::function<void(QQuickItem *)> walk = [&](QQuickItem *i) {
        if (!i)
            return;
        if (pred(i))
            out << i;
        for (QQuickItem *c : i->childItems())
            walk(c);
    };
    walk(root);
    return out;
}

inline bool isType(QObject *o, const char *qmlName)
{
    // QML-defined types report their class as "<Name>_QMLTYPE_<n>" (or with a _QML_ infix).
    return o && QByteArray(o->metaObject()->className()).startsWith(qmlName);
}

inline Taos::AppContext *g_currentContext = nullptr;

/// A QML app loaded against a controller. One Session per test process, as in a
/// real app; each load gets a fresh engine and a fresh `Taos` context (the
/// context singleton may belong to ONE engine), so tests do not share QML state.
class AppHost
{
public:
    AppHost(const QUrl &controller, const QString &user, const QString &password, const QString &appId)
        : m_session(hostOptions(controller)), m_appId(appId)
    {
        Taos::setDefaultClient(m_session.client());
        Taos::registerQmlTypes();
        qmlRegisterSingletonType<Taos::AppContext>("Taos", 1, 0, "Taos", [](QQmlEngine *, QJSEngine *) -> QObject * {
            return g_currentContext;
        });
        m_session.signIn(user, password);
    }
    ~AppHost() { unload(); }
    bool waitSignedIn()
    {
        return waitFor([this] { return m_session.state() == Taos::Session::SignedIn; }, 15000);
    }
    QQuickWindow *load(const QString &url)
    {
        unload();
        m_ctx = new Taos::AppContext(&m_session, m_appId);
        QQmlEngine::setObjectOwnership(m_ctx, QQmlEngine::CppOwnership);
        g_currentContext = m_ctx;
        m_engine = new QQmlEngine;
        m_ctx->setEngine(m_engine);
        m_engine->addImportPath(QStringLiteral("qrc:/taos/qml"));
        QQmlComponent comp(m_engine, QUrl(url));
        m_root = comp.create();
        if (!m_root) {
            qWarning().noquote() << "QML load failed:" << comp.errorString();
            return nullptr;
        }
        auto *w = qobject_cast<QQuickWindow *>(m_root);
        if (w) {
            w->show();
            QTest::qWaitForWindowExposed(w);
        }
        return w;
    }
    void unload()
    {
        delete m_root;
        m_root = nullptr;
        // The context holds the JS callbacks of requests still in flight: it must go
        // while the engine they belong to is alive.
        delete m_ctx;
        m_ctx = nullptr;
        delete m_engine;
        m_engine = nullptr;
        g_currentContext = nullptr;
    }
    Taos::Session &session() { return m_session; }

private:
    Taos::Session m_session;
    QString m_appId;
    Taos::AppContext *m_ctx = nullptr;
    QQmlEngine *m_engine = nullptr;
    QObject *m_root = nullptr;
};
