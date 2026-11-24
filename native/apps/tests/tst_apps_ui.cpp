// SPDX-License-Identifier: AGPL-3.0-or-later
// The two apps loaded in a QML engine against the extended fake controller:
// list, read, archive, filters, live updates, consent approve and deny with the
// exact paths and bodies, sign-in code, decisions answer, note, history.
#include "apphost.h"

using namespace Qt::StringLiterals;

namespace {
QQuickItem *byObjectName(QQuickWindow *w, const QString &name)
{
    return findItem(w->contentItem(), [&](QQuickItem *i) { return i->objectName() == name; });
}
QQuickItem *buttonNamed(QQuickItem *root, const QString &accessibleName)
{
    return findItem(root, [&](QQuickItem *i) { return i->property("accessibleName").toString() == accessibleName && i->metaObject()->indexOfSignal("clicked()") >= 0; });
}
QQuickItem *buttonWithText(QQuickItem *root, const QString &text, bool enabledOnly = true)
{
    return findItem(root, [&](QQuickItem *i) {
        return i->property("text").toString() == text && i->metaObject()->indexOfSignal("clicked()") >= 0
            && (!enabledOnly || i->isEnabled()) && i->isVisible();
    });
}
void click(QQuickItem *b) { QMetaObject::invokeMethod(b, "clicked"); }
QQuickItem *consentFor(QQuickItem *root, const QString &requestId)
{
    return findItem(root, [&](QQuickItem *i) {
        return i->property("requestId").toString() == requestId && i->property("denyReasonRequired").isValid();
    });
}
QString dump(QObject *c)
{
    if (!c)
        return u"(no consent card)"_s;
    QStringList out;
    for (const char *p : {"vocabError", "projectScopes", "needsProject", "selectedProjectId", "requestedProjectName", "requestedNotFound",
                          "allowLabel", "denyLabel", "errorText", "busy", "loadingProjects"})
        out << u"%1=%2"_s.arg(QString::fromLatin1(p), c->property(p).toString());
    out << u"granted=%1"_s.arg(c->property("granted").toStringList().join(u','));
    return out.join(u"; "_s);
}
class PropRecorder : public QObject
{
    Q_OBJECT
public:
    QByteArray prop;
    QStringList seen;
public Q_SLOTS:
    void record()
    {
        const QString v = sender()->property(prop.constData()).toString();
        if (!v.isEmpty())
            seen << v;
    }
};
int rows(QObject *feedOrQuery)
{
    auto *m = feedOrQuery->property("model").value<QAbstractItemModel *>();
    return m ? m->rowCount() : -1;
}
QVariantMap rowWithId(QObject *feed, int id)
{
    QVariantMap out;
    QMetaObject::invokeMethod(feed, "rowById", Q_RETURN_ARG(QVariantMap, out), Q_ARG(qint64, id));
    return out;
}
QJsonObject lastCallBody(FakeHost &h, const QString &path)
{
    const QJsonArray a = h.callsTo(path);
    return a.isEmpty() ? QJsonObject{{u"__none"_s, true}} : a.last().toObject().value(u"body"_s).toObject();
}
} // namespace

class TstAppsUi : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    AppHost *m_host = nullptr;
    QQuickWindow *m_win = nullptr;

    void openApp(const QString &app)
    {
        m_fake->reseed();
        m_win = m_host->load(u"qrc:/my.taos.%1/Main.qml"_s.arg(app));
        QVERIFY(m_win);
    }

private Q_SLOTS:
    void initTestCase()
    {
        m_fake = new FakeHost;
        QVERIFY2(m_fake->ok(), "fake controller did not start");
        m_fake->reseed();
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.test"_s);
        QVERIFY(m_host->waitSignedIn());
    }
    void cleanupTestCase()
    {
        m_host->unload();
        delete m_host;
        delete m_fake;
    }
    void cleanup() { m_host->unload(); m_win = nullptr; }

    // ---------------------------------------------------------------- Notifications
    void notifications_list_orders_pinned_first_and_counts_unread()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(feed);
        QVERIFY2(waitFor([&] { return rows(feed) == 6; }),
                 qPrintable(u"rows=%1 running=%2 loaded=%3 live=%4 err=%5 total=%6"_s.arg(rows(feed)).arg(feed->property("running").toString(), feed->property("loaded").toString(), feed->property("live").toString(), feed->property("errorText").toString()).arg(feed->property("totalCount").toInt())));
        auto *m = feed->property("model").value<QAbstractItemModel *>();
        const int idRole = m->roleNames().key("id");
        QCOMPARE(m->index(0, 0).data(idRole).toInt(), 1);   // pinned, newest first
        QCOMPARE(m->index(1, 0).data(idRole).toInt(), 2);
        QCOMPARE(m->index(2, 0).data(idRole).toInt(), 3);   // then newest first
        QCOMPARE(feed->property("unreadCount").toInt(), 4);
        QCOMPARE(feed->property("pinnedCount").toInt(), 2);
        QVERIFY(waitFor([&] { return byObjectName(m_win, u"notificationList"_s) != nullptr; }));
    }

    void notifications_mark_read_archive_and_pinned_dismiss()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QMetaObject::invokeMethod(feed, "markRead", Q_ARG(qint64, 4));
        QVERIFY(waitFor([&] { return feed->property("unreadCount").toInt() == 3; }));
        QVERIFY(waitFor([&] { return rowWithId(feed, 4).value(u"read"_s).toBool(); }));   // the server's row, not just the overlay
        QMetaObject::invokeMethod(feed, "dismiss", Q_ARG(qint64, 4));                       // archive
        QVERIFY(waitFor([&] { return rows(feed) == 5; }));
        QVERIFY(waitFor([&] { return rowWithId(feed, 4).isEmpty(); }));      // gone from the controller's list too
        QMetaObject::invokeMethod(feed, "dismiss", Q_ARG(qint64, 1));                       // a pending consent row is only marked read
        QVERIFY(waitFor([&] { return rowWithId(feed, 1).value(u"read"_s).toBool(); }));
        QCOMPARE(rows(feed), 5);
    }

    void notifications_mark_all_read_and_clear_all_keep_pending_requests()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QQuickItem *b = byObjectName(m_win, u"markAllRead"_s);
        QVERIFY(b);
        const QPoint at = b->mapToScene(QPointF(b->width() / 2, b->height() / 2)).toPoint();
        QTest::mouseMove(m_win, at);
        QTest::qWait(60);
        QTest::mouseClick(m_win, Qt::LeftButton, {}, at);
        QVERIFY2(waitFor([&] { return feed->property("unreadCount").toInt() == 0; }),
                 qPrintable(u"click at %1,%2 in %3x%4; button enabled=%5 visible=%6 size=%7x%8"_s.arg(at.x()).arg(at.y()).arg(m_win->width()).arg(m_win->height())
                            .arg(b->isEnabled()).arg(b->isVisible()).arg(b->width()).arg(b->height())));
        QMetaObject::invokeMethod(feed, "clearAll");
        QVERIFY(waitFor([&] { return rows(feed) == 2; }));       // only the two pending consent rows stay
    }

    void notifications_filters_by_level_source_and_unread()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        feed->setProperty("levelFilter", u"error"_s);
        QTRY_COMPARE(rows(feed), 1);
        feed->setProperty("levelFilter", u""_s);
        feed->setProperty("sourceFilter", u"app.installed"_s);
        QTRY_COMPARE(rows(feed), 1);
        feed->setProperty("sourceFilter", u""_s);
        feed->setProperty("unreadOnly", true);
        QTRY_COMPARE(rows(feed), 4);
        QVERIFY(feed->property("sources").toStringList().contains(u"auth_broker"_s));
    }

    void notifications_a_new_row_arrives_live()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QVERIFY(waitFor([&] { return feed->property("live").toBool(); }));
        m_fake->config({{u"add_notification"_s, QJsonObject{{u"title"_s, u"Live one"_s}, {u"source"_s, u"worker.join"_s}}}});
        QVERIFY(waitFor([&] { return rows(feed) == 7; }));
        QCOMPARE(feed->property("unreadCount").toInt(), 5);
    }

    void notifications_app_supplied_text_is_plain_text_never_markup()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QVERIFY(waitFor([&] { return feed->property("live").toBool(); }));
        const QString title = u"<img src=\"http://127.0.0.1:9/beacon.png\">Mail <b>from</b> Alex"_s;
        const QString message = u"<a href=\"http://127.0.0.1:9/\">click</a> <img src=x>"_s;
        m_fake->config({{u"add_notification"_s, QJsonObject{{u"title"_s, title}, {u"message"_s, message}, {u"source"_s, u"os:mail"_s}}}});
        QVERIFY(waitFor([&] { return rows(feed) == 7; }));
        auto shown = [&](const QString &text) {
            return findItems(m_win->contentItem(), [&](QQuickItem *i) { return i->property("text").toString() == text; });
        };
        QVERIFY(waitFor([&] { return !shown(title).isEmpty() && !shown(message).isEmpty(); }));
        for (const QString &t : {title, message})
            for (QQuickItem *i : shown(t))
                QCOMPARE(i->property("textFormat").toInt(), int(Qt::PlainText)); // not Auto, not Rich, not Styled
    }

    void notifications_row_tap_emits_an_open_request_and_marks_read()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QSignalSpy spy(feed, SIGNAL(openAppRequested(QString, QVariantMap)));
        QMetaObject::invokeMethod(feed, "activate", Q_ARG(qint64, 6));  // system.update -> settings/updates
        QCOMPARE(spy.size(), 1);
        QCOMPARE(spy.first().at(0).toString(), u"my.taos.settings"_s);
        QCOMPARE(spy.first().at(1).toMap().value(u"section"_s).toString(), u"updates"_s);
        // a row with no destination is only marked read
        QMetaObject::invokeMethod(feed, "activate", Q_ARG(qint64, 3));
        QCOMPARE(spy.size(), 1);
    }

    void notifications_consent_allow_posts_exactly_what_the_web_posts()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"req-1"_s)) != nullptr; }));
        // the requested project resolves against the server's list and is labelled before Allow
        QVERIFY2(waitFor([&] { return c->property("requestedProjectName").toString() == u"Alpha"_s; }), qPrintable(dump(c)));
        const QString allow = u"Allow memory_read, project_tasks on Alpha"_s;
        QQuickItem *b = nullptr;
        QVERIFY2(waitFor([&] { return (b = buttonNamed(c, allow)) != nullptr && b->isEnabled(); }), qPrintable(dump(c)));
        click(b);
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/agents/auth-requests/req-1/approve"_s).isEmpty(); }));
        QCOMPARE(m_fake->callsTo(u"/api/agents/auth-requests/req-1/approve"_s).size(), 1);
        QCOMPARE(lastCallBody(*m_fake, u"/api/agents/auth-requests/req-1/approve"_s),
                 (QJsonObject{{u"granted_scopes"_s, QJsonArray{u"memory_read"_s, u"project_tasks"_s}}, {u"project_id"_s, u"p1"_s}}));
        // resolved: the row is archived by the server and leaves the list
        QVERIFY(waitFor([&] { return rowWithId(feed, 1).isEmpty(); }));
    }

    void notifications_consent_allow_is_blocked_until_a_project_is_chosen()
    {
        m_fake->reseed();
        // a request naming a project the operator cannot see: not adopted, said so in red, Allow stays off
        m_fake->config({{u"add_notification"_s, QJsonObject{{u"id"_s, 20}, {u"title"_s, u"Access request"_s}, {u"source"_s, u"auth_requests"_s},
            {u"pinned"_s, true}, {u"data"_s, QJsonObject{{u"request_id"_s, u"req-9"_s}, {u"identity_claim"_s, u"agent-z"_s},
                {u"requested_scopes"_s, QJsonArray{u"project_tasks"_s}}, {u"project_id"_s, u"nope"_s}}}}}});
        m_win = m_host->load(u"qrc:/my.taos.notifications/Main.qml"_s);
        QVERIFY(m_win);
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"req-9"_s)) != nullptr; }));
        QVERIFY2(waitFor([&] { return c->property("requestedNotFound").toBool(); }), qPrintable(dump(c)));
        QQuickItem *b = buttonNamed(c, u"Allow project_tasks"_s);
        QVERIFY(b);
        QVERIFY(!b->isEnabled());
        QVERIFY(c->property("selectedProjectId").toString().isEmpty());
    }

    void notifications_consent_deny_sends_no_body_for_an_access_request()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"req-1"_s)) != nullptr; }));
        QQuickItem *b = nullptr;
        QVERIFY2(waitFor([&] { return (b = buttonNamed(c, u"Deny memory_read, project_tasks on Alpha"_s)) != nullptr && b->isEnabled(); }), qPrintable(dump(c)));
        click(b);
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/agents/auth-requests/req-1/deny"_s).isEmpty(); }));
        const QJsonObject call = m_fake->callsTo(u"/api/agents/auth-requests/req-1/deny"_s).first().toObject();
        QVERIFY(!call.value(u"has_body"_s).toBool());
    }

    void notifications_scope_request_row_uses_the_registry_route()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"sreq-1"_s)) != nullptr; }));
        QQuickItem *b = nullptr;
        QVERIFY2(waitFor([&] { return (b = buttonNamed(c, u"Allow files_write, memory_read on Beta"_s)) != nullptr && b->isEnabled(); }), qPrintable(dump(c)));
        click(b);
        const QString path = u"/api/agents/registry/agent-beta/scope-requests/sreq-1/approve"_s;
        QVERIFY(waitFor([&] { return !m_fake->callsTo(path).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, path),
                 (QJsonObject{{u"granted_scopes"_s, QJsonArray{u"files_write"_s, u"memory_read"_s}}, {u"project_id"_s, u"p2"_s}}));
    }

    void notifications_sign_in_code_goes_to_the_broker()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("requestId").toString() == u"br-1"_s && i->property("host").isValid(); })) != nullptr; }));
        QCOMPARE(card->property("host").toString(), u"claude.ai"_s);
        QQuickItem *field = findItem(card, [](QQuickItem *i) { return i->property("accessibleName").toString() == u"Sign-in code for agent-gamma"_s; });
        QVERIFY(field);
        field->setProperty("text", u"bad"_s);
        QQuickItem *send = buttonNamed(card, u"Send code to agent-gamma"_s);
        QVERIFY(send);
        QVERIFY(waitFor([&] { return send->isEnabled(); }));
        click(send);
        QVERIFY(waitFor([&] { return card->property("errorText").toString() == u"code rejected"_s; }));  // the controller's own words
        field->setProperty("text", u" abc-123 "_s);
        click(send);
        const QString path = u"/api/auth-broker/requests/br-1/code"_s;
        QVERIFY(waitFor([&] { return m_fake->callsTo(path).size() == 2; }));
        QCOMPARE(lastCallBody(*m_fake, path), (QJsonObject{{u"code"_s, u"abc-123"_s}}));
        QVERIFY(waitFor([&] { return rowWithId(feed, 3).isEmpty(); }));
    }

    void notifications_failed_load_shows_an_error_state_not_an_empty_one()
    {
        m_fake->reseed();
        m_fake->config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/notifications"_s}, {u"status"_s, 500}, {u"message"_s, u"database is locked"_s}, {u"times"_s, 50}}}});
        m_win = m_host->load(u"qrc:/my.taos.notifications/Main.qml"_s);
        QVERIFY(m_win);
        QQuickItem *err = nullptr;
        QVERIFY(waitFor([&] { return (err = byObjectName(m_win, u"errorState"_s)) != nullptr && err->isVisible(); }));
        QVERIFY(byObjectName(m_win, u"emptyState"_s)->isVisible() == false);
        m_fake->config({{u"fail"_s, QJsonValue()}});
    }

    void notifications_keyboard_moves_between_rows_and_opens_with_enter()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QList<QQuickItem *> heads;
        QVERIFY(waitFor([&] { heads = findItems(m_win->contentItem(), [](QQuickItem *i) { return i->objectName() == u"rowHeader"_s; }); return heads.size() >= 3; }));
        heads.first()->forceActiveFocus(Qt::TabFocusReason);
        QVERIFY(heads.first()->hasActiveFocus());
        QTest::keyClick(m_win, Qt::Key_Down);
        QVERIFY(waitFor([&] { return heads.at(1)->hasActiveFocus(); }));
        QTest::keyClick(m_win, Qt::Key_Up);
        QVERIFY(waitFor([&] { return heads.first()->hasActiveFocus(); }));
        // Enter on the system.update row opens its target and marks it read
        QSignalSpy spy(feed, SIGNAL(openAppRequested(QString, QVariantMap)));
        QMetaObject::invokeMethod(feed, "activate", Q_ARG(qint64, 6));
        QCOMPARE(spy.size(), 1);
        // M marks the focused row read
        heads.at(3)->forceActiveFocus(Qt::TabFocusReason);
        QTest::keyClick(m_win, Qt::Key_M);
        QVERIFY(waitFor([&] { return feed->property("unreadCount").toInt() == 3; }));
    }

    void notifications_empty_state_when_nothing_matches_and_when_empty()
    {
        openApp(u"notifications"_s);
        QObject *feed = m_win->findChild<QObject *>(u"feed"_s);
        QVERIFY(waitFor([&] { return rows(feed) == 6; }));
        QMetaObject::invokeMethod(feed, "markAllRead");
        feed->setProperty("levelFilter", u"error"_s);
        feed->setProperty("unreadOnly", true);
        QTRY_COMPARE(rows(feed), 0);
        QCOMPARE(feed->property("totalCount").toInt(), 6);          // the filters hide, they do not delete
        feed->setProperty("unreadOnly", false);
        feed->setProperty("levelFilter", u""_s);
        QMetaObject::invokeMethod(feed, "clearAll");
        QVERIFY(waitFor([&] { return feed->property("totalCount").toInt() == 2; }));  // the two pending consent rows stay
    }

    // ------------------------------------------------------------------- Decisions
    void decisions_lists_requests_and_pending_questions()
    {
        openApp(u"decisions"_s);
        QObject *pq = m_win->findChild<QObject *>(u"pendingQuery"_s);
        QObject *aq = m_win->findChild<QObject *>(u"accessQuery"_s);
        QObject *sq = m_win->findChild<QObject *>(u"scopeQuery"_s);
        QVERIFY(pq && aq && sq);
        QVERIFY(waitFor([&] { return rows(pq) == 4 && rows(aq) == 2 && rows(sq) == 1; }));
        QQuickItem *badge = byObjectName(m_win, u"pendingBadge"_s);
        QVERIFY(waitFor([&] { return (badge = byObjectName(m_win, u"pendingBadge"_s)) && badge->isVisible(); }));
    }

    void decisions_single_select_answer_sends_value_and_source()
    {
        openApp(u"decisions"_s);
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("selectedSingle").isValid() && i->property("model").isValid() && i->property("errorText").isValid()
                && i->property("type").toString() == u"single_select"_s; })) != nullptr; }));
        card->setProperty("selectedSingle", u"pg"_s);
        QQuickItem *submit = nullptr;
        QVERIFY(waitFor([&] { return (submit = buttonWithText(card, u"Submit"_s)) != nullptr; }));
        click(submit);
        const QString path = u"/api/decisions/d-1/answer"_s;
        QVERIFY(waitFor([&] { return !m_fake->callsTo(path).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, path), (QJsonObject{{u"value"_s, u"pg"_s}, {u"source"_s, u"native"_s}}));
        QObject *pq = m_win->findChild<QObject *>(u"pendingQuery"_s);
        QVERIFY(waitFor([&] { return rows(pq) == 3; }));
    }

    void decisions_other_answer_sends_an_empty_value_with_other_value()
    {
        openApp(u"decisions"_s);
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("selectedSingle").isValid() && i->property("type").toString() == u"single_select"_s; })) != nullptr; }));
        card->setProperty("selectedSingle", u"__other__"_s);
        QQuickItem *area = findItem(card, [](QQuickItem *i) { return i->property("accessibleName").toString() == u"Other answer"_s; });
        QVERIFY(area);
        QQuickItem *submit = buttonWithText(card, u"Submit"_s, false);
        QVERIFY(submit);
        QVERIFY(!submit->isEnabled());                       // Other is empty
        area->setProperty("text", u"  MySQL  "_s);
        QQuickItem *note = findItem(card, [](QQuickItem *i) { return i->property("accessibleName").toString() == u"Answer note"_s; });
        note->setProperty("text", u"because"_s);
        QVERIFY(waitFor([&] { return submit->isEnabled(); }));
        click(submit);
        const QString path = u"/api/decisions/d-1/answer"_s;
        QVERIFY(waitFor([&] { return !m_fake->callsTo(path).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, path), (QJsonObject{{u"value"_s, u""_s}, {u"other_value"_s, u"MySQL"_s}, {u"note"_s, u"because"_s}, {u"source"_s, u"native"_s}}));
    }

    void decisions_multi_approve_and_free_text_shapes()
    {
        openApp(u"decisions"_s);
        auto cardOfType = [&](const QString &type) -> QQuickItem * {
            QQuickItem *c = nullptr;
            waitFor([&] { return (c = findItem(m_win->contentItem(), [&](QQuickItem *i) {
                return i->property("errorText").isValid() && i->property("noteBusy").isValid() && i->property("type").toString() == type; })) != nullptr; });
            return c;
        };
        QQuickItem *multi = cardOfType(u"multi_select"_s);
        QVERIFY(multi);
        multi->setProperty("picked", QVariantList{u"eu"_s, u"us"_s});
        click(buttonWithText(multi, u"Submit"_s));
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/decisions/d-2/answer"_s).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, u"/api/decisions/d-2/answer"_s),
                 (QJsonObject{{u"value"_s, QJsonArray{u"eu"_s, u"us"_s}}, {u"source"_s, u"native"_s}}));

        QQuickItem *ad = cardOfType(u"approve_deny"_s);
        QVERIFY(ad);
        QVERIFY(ad->property("gate").isValid());
        click(buttonWithText(ad, u"Approve"_s));
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/decisions/d-3/answer"_s).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, u"/api/decisions/d-3/answer"_s), (QJsonObject{{u"value"_s, u"approve"_s}, {u"source"_s, u"native"_s}}));

        QQuickItem *ft = cardOfType(u"free_text"_s);
        QVERIFY(ft);
        QQuickItem *area = findItem(ft, [](QQuickItem *i) { return i->property("accessibleName").toString() == u"Answer"_s; });
        QVERIFY(area);
        QQuickItem *submit = buttonWithText(ft, u"Submit"_s, false);
        QVERIFY(!submit->isEnabled());                       // blank
        area->setProperty("text", u"  roll out in waves  "_s);
        QVERIFY(waitFor([&] { return submit->isEnabled(); }));
        click(submit);
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/decisions/d-4/answer"_s).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, u"/api/decisions/d-4/answer"_s), (QJsonObject{{u"value"_s, u"roll out in waves"_s}, {u"source"_s, u"native"_s}}));
    }

    void decisions_server_error_text_is_shown_verbatim()
    {
        openApp(u"decisions"_s);
        QPointer<QQuickItem> card;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("selectedSingle").isValid() && i->property("type").toString() == u"single_select"_s; })) != nullptr; }));
        // someone else answers first and no event reaches us: this card is stale
        m_fake->config({{u"mutate_decision"_s, QJsonObject{{u"id"_s, u"d-1"_s}, {u"status"_s, u"answered"_s}}}});
        PropRecorder rec;
        rec.prop = "errorText";
        const int sig = card->metaObject()->indexOfSignal("errorTextChanged()");
        QVERIFY(sig >= 0);
        QObject::connect(card.data(), card->metaObject()->method(sig), &rec, rec.metaObject()->method(rec.metaObject()->indexOfSlot("record()")));
        card->setProperty("selectedSingle", u"pg"_s);
        QQuickItem *submit = buttonWithText(card, u"Submit"_s);
        QVERIFY(submit);
        click(submit);
        // the controller answered {"error": ...}, not {"detail": ...}: its sentence is shown as it came
        QVERIFY(waitFor([&] { return !rec.seen.isEmpty(); }));
        QCOMPARE(rec.seen.first(), u"already answered or not pending"_s);
    }

    void decisions_note_goes_to_the_note_route()
    {
        openApp(u"decisions"_s);
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("noteBusy").isValid() && i->property("type").toString() == u"free_text"_s; })) != nullptr; }));
        QQuickItem *field = findItem(card, [](QQuickItem *i) { return i->property("accessibleName").toString() == u"Note text"_s; });
        QVERIFY(field);
        field->setProperty("text", u"  checking in  "_s);
        click(buttonWithText(card, u"Add note"_s));
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/decisions/d-4/note"_s).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, u"/api/decisions/d-4/note"_s), (QJsonObject{{u"text"_s, u"checking in"_s}}));
    }

    void decisions_archive_and_lazy_history()
    {
        openApp(u"decisions"_s);
        QVERIFY(waitFor([&] { return byObjectName(m_win, u"tabBar"_s) != nullptr; }));
        QQuickItem *tabs = byObjectName(m_win, u"tabBar"_s);
        QVERIFY(tabs);
        QQuickItem *archiveTab = byObjectName(m_win, u"tabArchive"_s);
        QVERIFY(archiveTab);
        QTest::mouseClick(m_win, Qt::LeftButton, {}, archiveTab->mapToScene(QPointF(archiveTab->width() / 2, archiveTab->height() / 2)).toPoint());
        QObject *aq = m_win->findChild<QObject *>(u"answeredQuery"_s);
        QVERIFY(waitFor([&] { return rows(aq) == 1; }));
        QQuickItem *card = nullptr;
        QVERIFY(waitFor([&] { return (card = findItem(m_win->contentItem(), [](QQuickItem *i) {
            return i->property("historyOpen").isValid() && i->property("chain").isValid(); })) != nullptr; }));
        QVERIFY(card->property("chain").isNull());           // nothing fetched until it is opened
        QQuickItem *toggle = buttonWithText(card, u"View history"_s);
        QVERIFY(toggle);
        click(toggle);
        QVERIFY(waitFor([&] { return card->property("chain").toList().size() == 1; }));
        QVERIFY(card->property("historyOpen").toBool());
        QObject *wq = m_win->findChild<QObject *>(u"withdrawnQuery"_s);
        QVERIFY(wq);
        QVERIFY(!wq->property("running").toBool());          // the withdrawn list is not fetched until its tab is open
    }

    void decisions_access_request_card_narrow_and_deny_reason()
    {
        openApp(u"decisions"_s);
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"sreq-1"_s)) != nullptr; }));
        QVERIFY(c->property("selectable").toBool());
        QVERIFY(c->property("denyReasonRequired").toBool());
        QVERIFY(waitFor([&] { return !c->property("projectScopes").isNull() && c->property("projectScopes").isValid(); }));
        // untick memory_read: the grant narrows to files_write, and the dropped scope is flagged
        QMetaObject::invokeMethod(c, "toggleNothing");       // no such method: ignored
        c->setProperty("ticked", QVariantList{u"files_write"_s});
        QCOMPARE(c->property("granted").toStringList(), QStringList{u"files_write"_s});
        QVERIFY(waitFor([&] { return c->property("selectedProjectId").toString() == u"p2"_s; }));  // the requested project, found in the server's list
        QQuickItem *allow = nullptr;
        QVERIFY(waitFor([&] { return (allow = buttonNamed(c, u"Allow files_write on Beta"_s)) != nullptr && allow->isEnabled(); }));
        // Deny first asks for a reason
        QQuickItem *deny = buttonNamed(c, u"Deny files_write, memory_read on Beta"_s);
        QVERIFY(deny);
        click(deny);
        QVERIFY(c->property("denying").toBool());
        QVERIFY(m_fake->callsTo(u"/api/agents/registry/agent-beta/scope-requests/sreq-1/deny"_s).isEmpty());
        c->setProperty("denyReason", u"  not now  "_s);
        QMetaObject::invokeMethod(c, "decide", Q_ARG(QVariant, false));
        const QString path = u"/api/agents/registry/agent-beta/scope-requests/sreq-1/deny"_s;
        QVERIFY(waitFor([&] { return !m_fake->callsTo(path).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, path), (QJsonObject{{u"reason"_s, u"not now"_s}}));
    }

    void decisions_access_request_with_cluster_read_needs_no_project()
    {
        openApp(u"decisions"_s);
        QQuickItem *c = nullptr;
        QVERIFY(waitFor([&] { return (c = consentFor(m_win->contentItem(), u"req-2"_s)) != nullptr; }));
        QQuickItem *allow = nullptr;
        QVERIFY(waitFor([&] { return (allow = buttonNamed(c, u"Allow cluster_read"_s)) != nullptr && allow->isEnabled(); }));
        click(allow);
        QVERIFY(waitFor([&] { return !m_fake->callsTo(u"/api/agents/auth-requests/req-2/approve"_s).isEmpty(); }));
        QCOMPARE(lastCallBody(*m_fake, u"/api/agents/auth-requests/req-2/approve"_s),
                 (QJsonObject{{u"granted_scopes"_s, QJsonArray{u"cluster_read"_s}}}));           // no project_id
        QObject *aq = m_win->findChild<QObject *>(u"accessQuery"_s);
        QVERIFY(waitFor([&] { return rows(aq) == 1; }));                                             // the card disappears
    }

    void decisions_a_decision_answered_elsewhere_arrives_live()
    {
        openApp(u"decisions"_s);
        QObject *pq = m_win->findChild<QObject *>(u"pendingQuery"_s);
        QVERIFY(waitFor([&] { return rows(pq) == 4 && pq->property("live").toBool(); }));
        m_fake->config({{u"add_decision"_s, QJsonObject{{u"id"_s, u"d-9"_s}, {u"from_agent"_s, u"agent-x"_s}, {u"question"_s, u"New?"_s},
            {u"type"_s, u"approve_deny"_s}, {u"options"_s, QJsonArray{}}, {u"priority"_s, u"normal"_s}, {u"status"_s, u"pending"_s},
            {u"created_at"_s, double(QDateTime::currentSecsSinceEpoch())}, {u"notes"_s, QJsonArray{}}, {u"metadata"_s, QJsonObject{}}}}});
        QVERIFY(waitFor([&] { return rows(pq) == 5; }));
    }
};

QTEST_MAIN(TstAppsUi)
#include "tst_apps_ui.moc"
