// Unit tests of the pieces that decide nothing on the controller's behalf but must
// match the web app exactly: the routing table, the store's ordering and cap and
// filters, and the JS helpers of the consent component.
#include <QQmlComponent>
#include <QQmlEngine>
#include <QtTest>

#include "notificationfeed.h"
#include "taosqt/jsonmodel.h"

using namespace Qt::StringLiterals;
using namespace Taos::Apps;

namespace {
QJsonObject row(int id, double ts, const QString &source = u"test"_s, bool read = false, const QString &level = u"info"_s)
{
    return {{u"id"_s, id}, {u"timestamp"_s, ts}, {u"level"_s, level}, {u"title"_s, u"t%1"_s.arg(id)},
            {u"message"_s, u""_s}, {u"read"_s, read}, {u"source"_s, source}, {u"pinned"_s, false}};
}
const QStringList kRoles = {u"id"_s, u"timestamp"_s, u"level"_s, u"title"_s, u"message"_s,
                            u"read"_s, u"source"_s, u"data"_s, u"pinned"_s};
}

class TstAppsLogic : public QObject
{
    Q_OBJECT
    QVariant js(const QString &expr)
    {
        QQmlEngine engine;
        QQmlComponent c(&engine);
        c.setData((u"import QtQml\nimport \"qrc:/taos/qml/Taos/Apps/Logic.js\" as L\nQtObject { property var r: "_s + expr + u" }"_s)
                      .toUtf8(),
                  QUrl(u"qrc:/test.qml"_s));
        std::unique_ptr<QObject> o(c.create());
        if (!o) {
            qWarning() << c.errorString();
            return {};
        }
        return o->property("r");
    }

private Q_SLOTS:
    void routing_table()
    {
        QCOMPARE(routeFor(u"system.update"_s, {}).app, u"my.taos.settings"_s);
        QCOMPARE(routeFor(u"system.update"_s, {}).props.value(u"section"_s).toString(), u"updates"_s);
        QCOMPARE(routeFor(u"system.lifecycle"_s, {}).props.value(u"section"_s).toString(), u"updates"_s);
        QCOMPARE(routeFor(u"disk_quota"_s, {}).props.value(u"section"_s).toString(), u"storage"_s);
        for (const char *s : {"worker.join", "worker.online", "worker.leave", "backend.up", "backend.down"})
            QCOMPARE(routeFor(QString::fromLatin1(s), {}).app, u"my.taos.cluster"_s);
        for (const char *s : {"training.complete", "training.failed", "agent_framework"})
            QCOMPARE(routeFor(QString::fromLatin1(s), {}).app, u"my.taos.agents"_s);
        QCOMPARE(routeFor(u"app.installed"_s, {}).app, u"my.taos.store"_s);
        QCOMPARE(routeFor(u"app.failed"_s, {}).app, u"my.taos.store"_s);
        QCOMPARE(routeFor(u"decisions"_s, {}).app, u"my.taos.decisions"_s);
        QCOMPARE(routeFor(u"auth_requests"_s, {}).app, u"my.taos.decisions"_s);
        QVERIFY(!routeFor(u"unknown.thing"_s, {}).valid()); // unknown targets just mark read
        // a typed target wins over the source
        const OpenTarget t = routeFor(u"system.update"_s,
            QJsonObject{{u"target"_s, QJsonObject{{u"kind"_s, u"project_file"_s}, {u"project_id"_s, u"p1"_s}, {u"path"_s, u"a/b.md"_s}}}});
        QCOMPARE(t.app, u"my.taos.projects"_s);
        QCOMPARE(t.props.value(u"projectId"_s).toString(), u"p1"_s);
        QCOMPARE(t.props.value(u"tab"_s).toString(), u"files"_s);
        QCOMPARE(t.props.value(u"filePath"_s).toString(), u"a/b.md"_s);
        QVERIFY(!routeFor(u"agent_scope_requests"_s, {}).valid()); // as the web table: no entry
    }

    void store_orders_pinned_first_caps_rest_and_filters()
    {
        Taos::JsonListModel src(kRoles);
        QJsonArray rows;
        for (int i = 1; i <= 130; ++i)
            rows.append(row(i, 1000 + i, u"s%1"_s.arg(i % 3), i % 2 == 0, i % 10 == 0 ? u"error"_s : u"info"_s));
        rows.append(row(500, 1, u"auth_requests"_s));           // the oldest row, but pinned
        rows.append(row(501, 2, u"agent_scope_requests"_s));
        src.setRows(rows);
        NotificationProxy p;
        p.setSourceModelChecked(&src);
        QTRY_COMPARE(p.rowCount(), 2 + 100);                     // pinned never trimmed, the rest capped at 100
        const int idRole = p.roleOf("id");
        QCOMPARE(p.index(0, 0).data(idRole).toInt(), 501);      // pinned first, newest first
        QCOMPARE(p.index(1, 0).data(idRole).toInt(), 500);
        QCOMPARE(p.index(2, 0).data(idRole).toInt(), 130);      // then newest first
        QCOMPARE(p.index(3, 0).data(idRole).toInt(), 129);
        QCOMPARE(p.pinnedCount(), 2);
        QCOMPARE(p.totalCount(), 102);

        p.setFilters(u"error"_s, {}, false);
        QCOMPARE(p.rowCount(), 10);                              // 30..130 step 10, inside the cap: 40..130
        p.setFilters({}, u"auth_requests"_s, false);
        QCOMPARE(p.rowCount(), 1);
        p.setFilters({}, {}, true);
        QVERIFY(p.rowCount() > 0);
        for (int r = 0; r < p.rowCount(); ++r)
            QVERIFY(!p.index(r, 0).data(p.roleOf("read")).toBool());
        p.setFilters(u"bogus"_s, {}, false);
        QCOMPARE(p.rowCount(), 0);
    }

    void overlay_applies_then_is_pruned_after_the_refetch()
    {
        Taos::JsonListModel src(kRoles);
        src.setRows({row(1, 100), row(2, 200)});
        NotificationProxy p;
        p.setSourceModelChecked(&src);
        QTRY_COMPARE(p.unreadCount(), 2);
        p.overlayRead(1, false);
        QTRY_COMPARE(p.unreadCount(), 1);
        p.overlayHide(2, false);
        QTRY_COMPARE(p.rowCount(), 1);
        p.settle(1);
        p.settle(2);
        src.setRows({row(1, 100, u"test"_s, true)});            // the server's answer arrives
        p.pruneSettled();
        QTRY_COMPARE(p.unreadCount(), 0);
        QCOMPARE(p.rowCount(), 1);
    }

    void relative_time_matches_the_web_function()
    {
        const double now = 1.8e12;
        QCOMPARE(js(u"L.relativeTime(%1 - 10000, %1)"_s.arg(now, 0, 'f', 0)).toString(), u"just now"_s);
        QCOMPARE(js(u"L.relativeTime((%1 - 5*60000)/1000, %1)"_s.arg(now, 0, 'f', 0)).toString(), u"5m ago"_s);   // seconds
        QCOMPARE(js(u"L.relativeTime(%1 - 3*3600000, %1)"_s.arg(now, 0, 'f', 0)).toString(), u"3h ago"_s);        // milliseconds
        QCOMPARE(js(u"L.relativeTime(%1 - 2*86400000, %1)"_s.arg(now, 0, 'f', 0)).toString(), u"2d ago"_s);
        QCOMPARE(js(u"L.relativeTime(new Date(%1 - 7200000).toISOString(), %1)"_s.arg(now, 0, 'f', 0)).toString(), u"2h ago"_s);
    }

    void consent_request_paths_and_bodies()
    {
        // approve an access request: granted_scopes, project_id only when needed, is_lead only when ticked and possible
        QCOMPARE(js(u"JSON.stringify(L.consentRequest(true, {source:'auth_requests', requestId:'r 1', granted:['a','b'], needsProject:false, projectId:'p', canLead:false, makeLead:true}))"_s).toString(),
                 u"{\"path\":\"/api/agents/auth-requests/r%201/approve\",\"body\":{\"granted_scopes\":[\"a\",\"b\"]}}"_s);
        QCOMPARE(js(u"JSON.stringify(L.consentRequest(true, {source:'auth_requests', requestId:'r', granted:['project_tasks'], needsProject:true, projectId:'p1', canLead:true, makeLead:true}))"_s).toString(),
                 u"{\"path\":\"/api/agents/auth-requests/r/approve\",\"body\":{\"granted_scopes\":[\"project_tasks\"],\"project_id\":\"p1\",\"is_lead\":true}}"_s);
        QCOMPARE(js(u"JSON.stringify(L.consentRequest(true, {source:'auth_requests', requestId:'r', granted:['project_tasks'], needsProject:true, projectId:'p1', canLead:true, makeLead:false}))"_s).toString(),
                 u"{\"path\":\"/api/agents/auth-requests/r/approve\",\"body\":{\"granted_scopes\":[\"project_tasks\"],\"project_id\":\"p1\"}}"_s);
        // deny: no body at all, unless a reason is required
        QVERIFY(js(u"L.consentRequest(false, {source:'auth_requests', requestId:'r', denyReasonRequired:false}).body === undefined"_s).toBool());
        QCOMPARE(js(u"JSON.stringify(L.consentRequest(false, {source:'agent_scope_requests', canonicalId:'a/b', requestId:'s', denyReasonRequired:true, denyReason:'  no  '}))"_s).toString(),
                 u"{\"path\":\"/api/agents/registry/a%2Fb/scope-requests/s/deny\",\"body\":{\"reason\":\"no\"}}"_s);
    }

    void scope_diff_and_vocabulary_validation()
    {
        QCOMPARE(js(u"JSON.stringify(L.computeScopeDiff(['a','b','c'],['a','x']))"_s).toString(),
                 u"{\"dropped\":[\"b\",\"c\"],\"added\":[\"x\"]}"_s);
        QVERIFY(js(u"L.projectScopesFrom({project_scopes:['a',1]}) === null"_s).toBool());   // one bad entry: the whole answer is malformed
        QVERIFY(js(u"L.projectScopesFrom({}) === null"_s).toBool());
        QCOMPARE(js(u"L.projectScopesFrom({project_scopes:['a']}).length"_s).toInt(), 1);
    }

    void sign_in_url_allowlist()
    {
        QVERIFY(!js(u"L.allowedSignInUrl('https://claude.ai/login')"_s).toString().isEmpty());
        QVERIFY(!js(u"L.allowedSignInUrl('https://console.anthropic.com:443/x')"_s).toString().isEmpty());
        for (const char *bad : {"http://claude.ai/", "https://evil.example/", "https://claude.ai.evil.example/",
                                "https://user@claude.ai/", "https://claude.ai:8443/", "https://claude.ai@evil.example/",
                                "https://claude.ai\\\\@evil.example/", "javascript:alert(1)", "https://claude.ai /x"})
            QVERIFY2(js(u"L.allowedSignInUrl(\"%1\")"_s.arg(QString::fromLatin1(bad).replace(u'\\', u"\\\\"_s))).toString().isEmpty(), bad);
    }

    void slug_matches_the_web_function()
    {
        QCOMPARE(js(u"L.slugifyClient('R\\u00e9sum\\u00e9 Project!')"_s).toString(), u"resume-project"_s);
        QVERIFY(js(u"L.slugifyWithFallback('\\u4e2d\\u6587', 'project')"_s).toString().startsWith(u"project-"_s));
        QCOMPARE(js(u"L.slugifyWithFallback('\\u4e2d\\u6587', 'project') === L.slugifyWithFallback('\\u4e2d\\u6587', 'project')"_s).toBool(), true);
        QCOMPARE(js(u"L.slugifyWithFallback('\\u4e2d', 'p') !== L.slugifyWithFallback('\\u6587', 'p')"_s).toBool(), true);
    }

    void gate_details_and_answer_label()
    {
        QCOMPARE(js(u"L.gateDetails({kind:'toString'})"_s).isNull(), true);
        QCOMPARE(js(u"L.gateDetails({kind:'execution_gate', agent_name:'a', tool:'', action_class:'w'}).rows.length"_s).toInt(), 2);
        QCOMPARE(js(u"L.answerLabel({options:[{label:'Postgres', value:'pg'}], answer:{value:['pg','x'], note:'ok'}})"_s).toString(),
                 u"Postgres, x (ok)"_s);
        QCOMPARE(js(u"L.answerLabel({options:[], answer:null})"_s).toString(), u"-"_s);
    }
};

QTEST_MAIN(TstAppsLogic)
#include "tst_apps_logic.moc"
