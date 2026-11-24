// SPDX-License-Identifier: AGPL-3.0-or-later
// Runs both apps against a REAL controller. Skipped unless TAOS_REAL_URL is set:
//   TAOS_REAL_URL=http://127.0.0.1:6978 TAOS_REAL_USER=u TAOS_REAL_PASSWORD=p tst_apps_real
// Use a scratch controller: it creates notifications, requests and decisions and
// approves, denies and answers them through the apps' own UI components.
#include "apphost.h"

using namespace Qt::StringLiterals;

namespace {
Taos::Result await(Taos::Reply *r)
{
    QSignalSpy spy(r, &Taos::Reply::finished);
    if (!spy.wait(20000))
        return Taos::Result{QJsonValue(), Taos::Error(Taos::Error::Internal, u"test timeout"_s)};
    return qvariant_cast<Taos::Result>(spy.first().first());
}
QQuickItem *buttonNamed(QQuickItem *root, const QString &name)
{
    return findItem(root, [&](QQuickItem *i) { return i->property("accessibleName").toString() == name && i->metaObject()->indexOfSignal("clicked()") >= 0; });
}
QQuickItem *consentFor(QQuickItem *root, const QString &requestId)
{
    return findItem(root, [&](QQuickItem *i) { return i->property("requestId").toString() == requestId && i->property("denyReasonRequired").isValid(); });
}
int rows(QObject *o)
{
    auto *m = o->property("model").value<QAbstractItemModel *>();
    return m ? m->rowCount() : -1;
}
bool hasRowWithTitle(QObject *feed, const QString &title)
{
    auto *m = feed->property("model").value<QAbstractItemModel *>();
    const int role = m->roleNames().key("title");
    for (int r = 0; r < m->rowCount(); ++r)
        if (m->index(r, 0).data(role).toString() == title)
            return true;
    return false;
}
}

class TstAppsReal : public QObject
{
    Q_OBJECT
    AppHost *m_host = nullptr;
    Taos::Client *client() { return m_host->session().client(); }
    QUrl m_url;
    QString m_agentToken, m_agentCid;
    const QString m_tag = QString::number(QDateTime::currentSecsSinceEpoch());

    Taos::Result api(const QString &method, const QString &path, const QJsonValue &body = {}, const QJsonObject &query = {})
    {
        return await(client()->http(method, path, query, body));
    }
    // an external agent: no session, no token
    Taos::Result anon(const QString &method, const QString &path, const QJsonValue &body, const QString &bearer = {})
    {
        // one Client per credential for the whole run (creating and dropping one per poll is not what it is built for)
        Taos::Client *&c = m_clients[bearer];
        if (!c) {
            Taos::ClientOptions o;
            o.baseUrl = m_url;
            o.bearerToken = bearer;
            c = new Taos::Client(o, this);
        }
        return await(c->http(method, path, {}, body));
    }
    QHash<QString, Taos::Client *> m_clients;
    QString submitRequest(const QString &claim, const QStringList &scopes)
    {
        const Taos::Result r = anon(u"POST"_s, u"/api/agents/auth-requests"_s,
            QJsonObject{{u"identity_claim"_s, claim}, {u"framework"_s, u"openclaw"_s},
                        {u"requested_scopes"_s, QJsonArray::fromStringList(scopes)}, {u"reason"_s, u"native test"_s}});
        return r.value.toObject().value(u"request_id"_s).toString();
    }

private Q_SLOTS:
    void initTestCase()
    {
        if (qEnvironmentVariableIsEmpty("TAOS_REAL_URL"))
            QSKIP("TAOS_REAL_URL not set");
        m_url = QUrl(qEnvironmentVariable("TAOS_REAL_URL"));
        m_host = new AppHost(m_url, qEnvironmentVariable("TAOS_REAL_USER"), qEnvironmentVariable("TAOS_REAL_PASSWORD"), u"my.taos.test"_s);
        QVERIFY(m_host->waitSignedIn());
    }
    void cleanupTestCase() { delete m_host; }
    void cleanup() { if (m_host) m_host->unload(); }

    void notifications_list_read_archive_and_a_live_update()
    {
        const QString t1 = u"native real one %1"_s.arg(QDateTime::currentMSecsSinceEpoch());
        QVERIFY(api(u"POST"_s, u"/api/notifications"_s, QJsonObject{{u"title"_s, t1}, {u"message"_s, u"first"_s}, {u"level"_s, u"warning"_s}, {u"source"_s, u"backend.down"_s}}).ok());
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.notifications/Main.qml"_s);
        QVERIFY(w);
        QObject *feed = w->findChild<QObject *>(u"feed"_s);
        QVERIFY(feed);
        QVERIFY(waitFor([&] { return hasRowWithTitle(feed, t1); }, 15000));
        auto *m = feed->property("model").value<QAbstractItemModel *>();
        qint64 id = -1;
        for (int r = 0; r < m->rowCount(); ++r)
            if (m->index(r, 0).data(m->roleNames().key("title")).toString() == t1)
                id = m->index(r, 0).data(m->roleNames().key("id")).toLongLong();
        QVERIFY(id > 0);
        QMetaObject::invokeMethod(feed, "markRead", Q_ARG(qint64, id));
        // the controller's own row says read
        QVERIFY(waitFor([&] {
            for (const QJsonValue &v : api(u"GET"_s, u"/api/notifications"_s).value.toArray())
                if (v.toObject().value(u"id"_s).toInteger() == id)
                    return v.toObject().value(u"read"_s).toBool();
            return false;
        }, 15000));
        // live: a row created now arrives without a refresh
        const QString t2 = t1 + u" live"_s;
        QVERIFY(waitFor([&] { return feed->property("live").toBool(); }, 15000));
        QVERIFY(api(u"POST"_s, u"/api/notifications"_s, QJsonObject{{u"title"_s, t2}, {u"message"_s, u"second"_s}, {u"level"_s, u"info"_s}}).ok());
        QVERIFY(waitFor([&] { return hasRowWithTitle(feed, t2); }, 15000));
        // archive (dismiss) removes it on the controller
        QMetaObject::invokeMethod(feed, "dismiss", Q_ARG(qint64, id));
        QVERIFY(waitFor([&] { return !hasRowWithTitle(feed, t1); }, 15000));
        bool still = false;
        for (const QJsonValue &v : api(u"GET"_s, u"/api/notifications"_s).value.toArray())
            still = still || v.toObject().value(u"id"_s).toInteger() == id;
        QVERIFY(!still);
    }

    void consent_approve_and_deny_in_notifications()
    {
        const QString a = submitRequest(u"native-agent-a-%1"_s.arg(m_tag), {u"memory_read"_s});
        const QString b = submitRequest(u"native-agent-b-%1"_s.arg(m_tag), {u"memory_read"_s});
        QVERIFY(!a.isEmpty() && !b.isEmpty());
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.notifications/Main.qml"_s);
        QVERIFY(w);
        QQuickItem *ca = nullptr, *cb = nullptr;
        QVERIFY(waitFor([&] { return (ca = consentFor(w->contentItem(), a)) && (cb = consentFor(w->contentItem(), b)); }, 20000));
        // approve A: the Allow button is enabled only once the server's vocabulary is read
        QQuickItem *allow = nullptr;
        QVERIFY(waitFor([&] { return (allow = buttonNamed(ca, u"Allow memory_read"_s)) && allow->isEnabled(); }, 15000));
        QMetaObject::invokeMethod(allow, "clicked");
        QPointer<QQuickItem> pa(ca);   // the card goes away once the request is decided
        if (!waitFor([&] { return anon(u"GET"_s, u"/api/agents/auth-requests/%1"_s.arg(a), {}).value.toObject().value(u"status"_s).toString() == u"accepted"_s; }, 20000))
            QFAIL(qPrintable(u"not accepted; card error: '%1'"_s.arg(pa ? pa->property("errorText").toString() : u"(card gone)"_s)));
        const QJsonObject st = anon(u"GET"_s, u"/api/agents/auth-requests/%1"_s.arg(a), {}).value.toObject();
        m_agentToken = st.value(u"token"_s).toString();
        m_agentCid = st.value(u"canonical_id"_s).toString();
        QVERIFY2(!m_agentCid.isEmpty(), "approving minted an agent identity");
        // deny B
        QQuickItem *deny = nullptr;
        QVERIFY(waitFor([&] { return (deny = buttonNamed(cb, u"Deny memory_read"_s)) && deny->isEnabled(); }, 15000));
        QMetaObject::invokeMethod(deny, "clicked");
        QVERIFY(waitFor([&] { return anon(u"GET"_s, u"/api/agents/auth-requests/%1"_s.arg(b), {}).value.toObject().value(u"status"_s).toString() == u"refused"_s; }, 20000));
        // both rows leave the inbox
        QObject *feed = w->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return !consentFor(w->contentItem(), a) && !consentFor(w->contentItem(), b); }, 20000));
        Q_UNUSED(feed);
    }

    void consent_project_bound_scope_creates_the_project_inline()
    {
        const QString name = u"Native Inline %1"_s.arg(m_tag);
        const QString rid = submitRequest(u"native-agent-p-%1"_s.arg(m_tag), {u"project_tasks"_s});
        QVERIFY(!rid.isEmpty());
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.decisions/Main.qml"_s);
        QVERIFY(w);
        QPointer<QQuickItem> c;
        QVERIFY(waitFor([&] { return (c = consentFor(w->contentItem(), rid)) != nullptr; }, 20000));
        // the server's vocabulary says project_tasks needs a project (the picker shows)
        QVERIFY(waitFor([&] { return c->property("needsProject").toBool(); }, 15000));
        QQuickItem *allow = nullptr;
        // create a project inline: the card POSTs /api/projects {name, slug} and then approves with its id
        c->setProperty("creating", true);
        c->setProperty("newName", name);
        QVERIFY(waitFor([&] { return (allow = buttonNamed(c, u"Allow project_tasks on %1"_s.arg(name))) && allow->isEnabled(); }, 10000));
        QMetaObject::invokeMethod(allow, "clicked");
        if (!waitFor([&] { return anon(u"GET"_s, u"/api/agents/auth-requests/%1"_s.arg(rid), {}).value.toObject().value(u"status"_s).toString() == u"accepted"_s; }, 20000))
            QFAIL(qPrintable(u"not accepted; card error: '%1'"_s.arg(c ? c->property("errorText").toString() : u"(card gone)"_s)));
        const QJsonArray projects = api(u"GET"_s, u"/api/projects"_s, {}, QJsonObject{{u"status"_s, u"active"_s}}).value.toObject().value(u"items"_s).toArray();
        QString slug;
        for (const QJsonValue &v : projects)
            if (v.toObject().value(u"name"_s).toString() == name)
                slug = v.toObject().value(u"slug"_s).toString();
        qInfo() << "inline project slug:" << slug;
        QVERIFY2(!slug.isEmpty(), "the project was created by the consent card");
        QCOMPARE(slug, u"native-inline-%1"_s.arg(m_tag));
    }

    void decisions_scope_request_deny_with_reason_then_approve()
    {
        if (m_agentCid.isEmpty())
            QSKIP("needs the agent minted by the previous test");
        auto raise = [&]() {
            const Taos::Result r = anon(u"POST"_s, u"/api/agents/registry/%1/scope-requests"_s.arg(m_agentCid),
                QJsonObject{{u"requested_scopes"_s, QJsonArray{u"cluster_read"_s}}, {u"reason"_s, u"native test"_s}}, m_agentToken);
            if (!r.ok())
                qWarning() << "scope request:" << r.error.userMessage();
            return r.value.toObject().value(u"request_id"_s).toString();
        };
        const QString s1 = raise();
        QVERIFY(!s1.isEmpty());
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.decisions/Main.qml"_s);
        QVERIFY(w);
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(w->contentItem(), s1)) != nullptr; }, 20000));
        QQuickItem *deny = nullptr;
        QVERIFY(waitFor([&] { return (deny = buttonNamed(c, u"Deny cluster_read"_s)) != nullptr; }, 15000));
        QMetaObject::invokeMethod(deny, "clicked");
        QVERIFY(c->property("denying").toBool());                 // a reason is required first
        c->setProperty("denyReason", u"native test deny"_s);
        QMetaObject::invokeMethod(c, "decide", Q_ARG(QVariant, false));
        QVERIFY(waitFor([&] { return !consentFor(w->contentItem(), s1); }, 20000));
        const QString s2 = raise();
        QVERIFY(waitFor([&] { return (c = consentFor(w->contentItem(), s2)) != nullptr; }, 20000));
        QQuickItem *allow = nullptr;
        QVERIFY(waitFor([&] { return (allow = buttonNamed(c, u"Allow cluster_read"_s)) && allow->isEnabled(); }, 15000));
        QCOMPARE(c->property("needsProject").toBool(), false);    // cluster_read is global: no project picker
        QMetaObject::invokeMethod(allow, "clicked");
        QVERIFY(waitFor([&] { return !consentFor(w->contentItem(), s2); }, 20000));
    }

    void decisions_answer_goes_to_the_controller()
    {
        const Taos::Result created = api(u"POST"_s, u"/api/decisions"_s, QJsonObject{
            {u"from_agent"_s, u"native-agent-a"_s}, {u"question"_s, u"Pick a colour"_s}, {u"type"_s, u"single_select"_s},
            {u"options"_s, QJsonArray{QJsonObject{{u"label"_s, u"Blue"_s}, {u"value"_s, u"blue"_s}}, QJsonObject{{u"label"_s, u"Red"_s}, {u"value"_s, u"red"_s}}}}});
        QVERIFY2(created.ok(), qPrintable(created.error.userMessage()));
        const QString id = created.value.toObject().value(u"id"_s).toString();
        QVERIFY(!id.isEmpty());
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.decisions/Main.qml"_s);
        QVERIFY(w);
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(w->contentItem(), [&](QQuickItem *i) {
            return i->property("noteBusy").isValid() && i->property("model").isValid() && i->property("type").toString() == u"single_select"_s
                && i->property("errorText").isValid(); })) != nullptr; }, 20000));
        card->setProperty("selectedSingle", u"red"_s);
        QQuickItem *submit = findItem(card, [](QQuickItem *i) { return i->property("text").toString() == u"Submit"_s && i->metaObject()->indexOfSignal("clicked()") >= 0; });
        QVERIFY(submit);
        QMetaObject::invokeMethod(submit, "clicked");
        QVERIFY(waitFor([&] { return api(u"GET"_s, u"/api/decisions/%1"_s.arg(id)).value.toObject().value(u"status"_s).toString() == u"answered"_s; }, 20000));
        const QJsonObject d = api(u"GET"_s, u"/api/decisions/%1"_s.arg(id)).value.toObject();
        QCOMPARE(d.value(u"answer"_s).toObject().value(u"value"_s).toString(), u"red"_s);
        qInfo() << "recorded answer:" << QJsonDocument(d.value(u"answer"_s).toObject()).toJson(QJsonDocument::Compact);
        // a second answer is refused with the controller's own {"error": ...} sentence, which the app shows verbatim
        const Taos::Result again = api(u"POST"_s, u"/api/decisions/%1/answer"_s.arg(id), QJsonObject{{u"value"_s, u"blue"_s}, {u"source"_s, u"native"_s}});
        QVERIFY(!again.ok());
        qInfo() << "second answer is refused with:" << again.error.userMessage();
        QVERIFY(again.error.userMessage().contains(u"answered"_s, Qt::CaseInsensitive));
    }
};

QTEST_MAIN(TstAppsReal)
#include "tst_apps_real.moc"
