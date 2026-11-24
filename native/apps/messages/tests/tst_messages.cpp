// Messages: the pure logic, the two models, the engine against the fake controller
// (REST and the real /ws/chat through the Rust client) and the QML app loaded in an
// offscreen engine.
#include "apphost.h"
#include "chatengine.h"
#include "chatlogic.h"
#include "chatmodels.h"

using namespace Qt::StringLiterals;
using namespace Taos::Messages;

namespace {
QByteArray httpRaw(quint16 port, const char *method, const QByteArray &path, const QByteArray &body)
{
    QTcpSocket s;
    s.connectToHost(u"127.0.0.1"_s, port);
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
Msg msg(const QString &id, const QString &author, double at, const QString &content = u"x"_s, bool own = false,
        const QString &type = u"agent"_s)
{
    Msg m;
    m.id = id;
    m.authorId = author;
    m.authorType = own ? u"user"_s : type;
    m.createdAt = at;
    m.content = content;
    m.state = u"complete"_s;
    m.own = own;
    m.parts = MessageModel::partsFor(content, {});
    return m;
}
void typeText(QWindow *w, const QString &t)
{
    for (const QChar c : t)
        QTest::keyClick(w, c.toLatin1());
}
QStringList ids(const MessageModel &m)
{
    QStringList o;
    for (const Msg &x : m.msgs())
        o << (x.localId.isEmpty() ? x.id : x.localId);
    return o;
}
} // namespace

class TstMessages : public QObject
{
    Q_OBJECT
    FakeHost *m_fake = nullptr;
    quint16 m_port = 0;
    AppHost *m_host = nullptr;

    QJsonObject control(const QJsonObject &o)
    {
        return QJsonDocument::fromJson(httpRaw(m_port, "POST", "/__fake/chat", QJsonDocument(o).toJson(QJsonDocument::Compact)))
            .object();
    }
    QJsonObject state()
    {
        return QJsonDocument::fromJson(httpRaw(m_port, "GET", "/__fake/chat", {})).object();
    }
    QJsonArray posts() { return state().value(u"posts"_s).toArray(); }
    int countPosts(const QString &key, const QString &value, const QString &field = u"ws"_s)
    {
        int n = 0;
        for (const QJsonValue &v : posts())
            if (v.toObject().value(field).toString() == value && (key.isEmpty() || v.toObject().value(u"channel_id"_s).toString() == key))
                ++n;
        return n;
    }

private Q_SLOTS:
    // ---- pure logic ---------------------------------------------------------------------------
    void time_helpers()
    {
        QCOMPARE(toMs(1.7e9), qint64(1700000000000));
        QCOMPARE(toMs(1.7e12), qint64(1700000000000));
        const qint64 now = QDateTime::currentMSecsSinceEpoch();
        QCOMPARE(relativeTime(now / 1000.0 - 5, now), u"now"_s);
        QCOMPARE(relativeTime(now / 1000.0 - 180, now), u"3m"_s);
        QCOMPARE(relativeTime(now / 1000.0 - 7200, now).size(), 5); // HH:mm
        const QDateTime n = QDateTime::currentDateTime();
        QCOMPARE(dayLabel(n.toSecsSinceEpoch(), n), u"Today"_s);
        QCOMPARE(dayLabel(n.addDays(-1).toSecsSinceEpoch(), n), u"Yesterday"_s);
        QVERIFY(dayLabel(n.addDays(-5).toSecsSinceEpoch(), n).contains(u' '));
    }
    void run_grouping()
    {
        const double t = 1.76e9;
        QVERIFY(!breaksRun(u"a"_s, u"agent"_s, t, u"a"_s, u"agent"_s, t + 299));
        QVERIFY(breaksRun(u"a"_s, u"agent"_s, t, u"a"_s, u"agent"_s, t + 301));
        QVERIFY(breaksRun(u"a"_s, u"agent"_s, t, u"b"_s, u"agent"_s, t + 1));
        QVERIFY(breaksRun(u"a"_s, u"agent"_s, t, u"a"_s, u"user"_s, t + 1));
        QVERIFY(breaksRun(u"a"_s, u"agent"_s, t, u"a"_s, u"agent"_s, t + 86400 + 60)); // another day
    }
    void sections_follow_the_web_buckets()
    {
        const QList<AgentInfo> live{{u"alpha"_s, u"Alpha"_s, u"running"_s, {}}, {u"beta"_s, {}, u"paused"_s, {}},
                                    {u"gamma"_s, {}, u"failed"_s, {}}};
        const QSet<QString> arch{u"old"_s};
        auto b = [&](const QString &name, const QStringList &m, const QString &kind, bool loaded = true) {
            return bucketFor(name, m, kind, live, arch, loaded);
        };
        QCOMPARE(b(u"alpha"_s, {u"user"_s, u"alpha"_s}, {}), Bucket::Live);
        QCOMPARE(b(u"Alpha"_s, {u"user"_s, u"x"_s}, {}), Bucket::Live); // by display name
        QCOMPARE(b(u"beta"_s, {u"user"_s, u"beta"_s}, {}), Bucket::Suspended);
        QCOMPARE(b(u"gamma"_s, {u"user"_s, u"gamma"_s}, {}), Bucket::Archived); // failed is not live
        QCOMPARE(b(u"old"_s, {u"user"_s, u"old"_s}, {}), Bucket::Archived);
        QCOMPARE(b(u"ghost"_s, {u"user"_s, u"ghost"_s}, {}), Bucket::Archived); // orphan once lists loaded
        QCOMPARE(b(u"ghost"_s, {u"user"_s, u"ghost"_s}, {}, false), Bucket::NonAgent); // not before
        QCOMPARE(b(u"jo"_s, {u"user"_s}, {}), Bucket::NonAgent);
        QCOMPARE(b(u"a2a-x"_s, {u"alpha"_s, u"beta"_s}, u"a2a"_s), Bucket::NonAgent);
        QCOMPARE(sectionFor(u"topic"_s, Bucket::NonAgent), u"Channels"_s);
        QCOMPARE(sectionFor(u"group"_s, Bucket::NonAgent), u"Channels"_s);
        QCOMPARE(sectionFor(u"dm"_s, Bucket::Live), u"Agents-DMs"_s);
        QCOMPARE(sectionFor(u"dm"_s, Bucket::NonAgent), u"Direct Messages"_s);
        QCOMPARE(sectionFor(u"dm-remote"_s, Bucket::NonAgent), u"Remote"_s);
        QCOMPARE(presenceFor(u"running"_s, false), u"live"_s);
        QCOMPARE(presenceFor(u"running"_s, true), u"working"_s);
        QCOMPARE(presenceFor({}, false), u"idle"_s);
    }
    void mentions()
    {
        const QStringList members{u"user"_s, u"alpha"_s, u"beta"_s, u"alphonse"_s};
        QCOMPARE(mentionCandidates(members, u"al"_s), (QStringList{u"alpha"_s, u"alphonse"_s, u"all"_s}));
        QCOMPARE(mentionCandidates(members, u""_s), (QStringList{u"alpha"_s, u"beta"_s, u"alphonse"_s, u"all"_s}));
        QCOMPARE(mentionCandidates(members, u"ta"_s), (QStringList{u"beta"_s}));  // substring after prefix matches
        QCOMPARE(mentionCandidates(members, u"USER"_s), QStringList());          // never the person
        const MentionAt m = mentionAt(u"hi @be"_s, 6);
        QCOMPARE(m.atIndex, 3);
        QCOMPARE(m.partial, u"be"_s);
        QCOMPARE(mentionAt(u"mail me@x"_s, 9).atIndex, -1); // @ must start a word
        QCOMPARE(mentionAt(u"@"_s, 1).atIndex, 0);
    }
    void stall_watch_agent()
    {
        const QStringList agents{u"alpha"_s};
        QCOMPARE(pickWatchAgent(u"dm"_s, {u"user"_s, u"alpha"_s}, u"hi"_s, agents), u"alpha"_s);
        QCOMPARE(pickWatchAgent(u"topic"_s, {u"user"_s, u"alpha"_s}, u"hi"_s, agents), QString());
        QCOMPARE(pickWatchAgent(u"topic"_s, {u"user"_s, u"alpha"_s}, u"hi @alpha"_s, agents), u"alpha"_s);
        QCOMPARE(pickWatchAgent(u"dm"_s, {u"user"_s, u"jo"_s}, u"hi"_s, agents), QString()); // a person, not an agent
    }
    void markdown_is_sanitised()
    {
        auto h = [](const QString &s) { return markdownToHtml(s); };
        QVERIFY(h(u"<script>alert(1)</script>"_s).contains(u"&lt;script&gt;"_s));
        QVERIFY(!h(u"<img src=x onerror=1>"_s).contains(u"<img"_s));
        QVERIFY(!h(u"![x](http://evil/x.png)"_s).contains(u"<img"_s));
        QVERIFY(h(u"**b** and *i* and `c<d`"_s).contains(u"<b>b</b>"_s));
        QVERIFY(h(u"**b** and *i* and `c<d`"_s).contains(u"<i>i</i>"_s));
        QVERIFY(h(u"`c<d`"_s).contains(u"<code>c&lt;d</code>"_s));
        QVERIFY(!h(u"[x](javascript:alert(1))"_s).contains(u"href"_s));
        QVERIFY(h(u"[ok](https://example.org/a?b=1&c=2)"_s).contains(u"href=\"https://example.org/a?b=1&amp;c=2\""_s));
        QVERIFY(h(u"see https://example.org/x."_s).contains(u"href=\"https://example.org/x\""_s));
        QVERIFY(h(u"- a\n- b"_s).contains(u"<ul><li>a</li><li>b</li></ul>"_s));
        QVERIFY(h(u"1. a\n2. b"_s).contains(u"<ol>"_s));
        QVERIFY(h(u"> q"_s).contains(u"<blockquote>"_s));
        QVERIFY(h(u"| a | b |\n|---|---|\n| 1 | 2 |"_s).contains(u"<td>1</td>"_s));
        QVERIFY(h(u"snake_case_word and 2*3*4"_s).contains(u"snake_case_word"_s));
        QVERIFY(!h(u"snake_case_word"_s).contains(u"<i>"_s));
    }
    void fenced_code_segments()
    {
        QVariantList s = segments(u"before\n```bash\nls -l\n```\nafter"_s);
        QCOMPARE(s.size(), 3);
        QCOMPARE(s[1].toMap().value(u"kind"_s).toString(), u"code"_s);
        QCOMPARE(s[1].toMap().value(u"text"_s).toString(), u"ls -l"_s);
        QCOMPARE(s[1].toMap().value(u"lang"_s).toString(), u"bash"_s);
        // an unterminated fence (a streaming reply) is code, never lost
        s = segments(u"x\n```py\nprint(1"_s);
        QCOMPARE(s.last().toMap().value(u"kind"_s).toString(), u"code"_s);
        QCOMPARE(s.last().toMap().value(u"text"_s).toString(), u"print(1"_s);
        QVERIFY(isLongText(QString(2000, u'a')));
        QVERIFY(isLongText(QString(u"a\n").repeated(30)));
        QVERIFY(!isLongText(u"short"_s));
    }

    // ---- the message model --------------------------------------------------------------------
    void model_orders_groups_and_marks_days()
    {
        MessageModel m;
        const double t = double(QDateTime::currentSecsSinceEpoch()) - 3600;
        m.setAll({msg(u"c"_s, u"alpha"_s, t + 400), msg(u"a"_s, u"alpha"_s, t), msg(u"b"_s, u"alpha"_s, t + 60),
                  msg(u"d"_s, u"bob"_s, t + 410)}, 0);
        QCOMPARE(ids(m), (QStringList{u"a"_s, u"b"_s, u"c"_s, u"d"_s}));
        auto groupStart = [&](int r) { return m.data(m.index(r), MessageModel::GroupStartRole).toBool(); };
        QVERIFY(groupStart(0));
        QVERIFY(!groupStart(1));   // 60 s later, same author
        QVERIFY(groupStart(2));    // 340 s gap: a new run
        QVERIFY(groupStart(3));    // another author
        QCOMPARE(m.data(m.index(0), MessageModel::DayLabelRole).toString(), u"Today"_s);
        QCOMPARE(m.data(m.index(1), MessageModel::DayLabelRole).toString(), QString());
    }
    void model_dedupes_and_orders_late_arrivals()
    {
        MessageModel m;
        m.setAll({msg(u"a"_s, u"x"_s, 100), msg(u"c"_s, u"x"_s, 300)}, 0);
        QCOMPARE(m.add(msg(u"a"_s, u"x"_s, 100)), -1);
        QCOMPARE(m.add(msg(u"b"_s, u"x"_s, 200)), 1);
        QCOMPARE(ids(m), (QStringList{u"a"_s, u"b"_s, u"c"_s}));
    }
    void model_optimistic_send_success_and_race()
    {
        MessageModel m;
        m.setAll({msg(u"a"_s, u"x"_s, 100)}, 0);
        Msg l = msg({}, u"usr_1"_s, 200, u"hi"_s, true);
        l.localId = u"local-1"_s;
        l.state = u"sending"_s;
        m.add(l);
        QCOMPARE(m.data(m.index(1), MessageModel::StateRole).toString(), u"sending"_s);
        // the POST answered first: the row becomes the stored message
        m.replaceLocal(u"local-1"_s, msg(u"n1"_s, u"usr_1"_s, 200.5, u"hi"_s, true));
        QCOMPARE(ids(m), (QStringList{u"a"_s, u"n1"_s}));
        QVERIFY(!m.data(m.index(1), MessageModel::LocalRole).toBool());
        // the broadcast first: the local row is dropped, no duplicate
        Msg l2 = msg({}, u"usr_1"_s, 300, u"again"_s, true);
        l2.localId = u"local-2"_s;
        m.add(l2);
        m.add(msg(u"n2"_s, u"usr_1"_s, 300.1, u"again"_s, true));
        m.replaceLocal(u"local-2"_s, msg(u"n2"_s, u"usr_1"_s, 300.1, u"again"_s, true));
        QCOMPARE(ids(m), (QStringList{u"a"_s, u"n1"_s, u"n2"_s}));
    }
    void model_failed_send_and_retry_state()
    {
        MessageModel m;
        Msg l = msg({}, u"usr_1"_s, 200, u"hi"_s, true);
        l.localId = u"local-1"_s;
        l.state = u"sending"_s;
        m.add(l);
        m.setFailed(u"local-1"_s, u"address a member first"_s);
        QCOMPARE(m.data(m.index(0), MessageModel::StateRole).toString(), u"failed"_s);
        QCOMPARE(m.data(m.index(0), MessageModel::ErrorRole).toString(), u"address a member first"_s);
        m.setSending(u"local-1"_s);
        QCOMPARE(m.data(m.index(0), MessageModel::StateRole).toString(), u"sending"_s);
        QVERIFY(m.data(m.index(0), MessageModel::ErrorRole).toString().isEmpty());
    }
    void model_streaming_edit_delete_reactions()
    {
        MessageModel m;
        Msg a = msg(u"a"_s, u"alpha"_s, 100, u""_s);
        a.state = u"pending"_s;
        m.setAll({a}, 0);
        m.appendDelta(u"a"_s, u"Hel"_s);
        m.appendDelta(u"a"_s, u"lo"_s);
        QCOMPARE(m.data(m.index(0), MessageModel::ContentRole).toString(), u"Hello"_s);
        QCOMPARE(m.data(m.index(0), MessageModel::StateRole).toString(), u"streaming"_s);
        m.setState(u"a"_s, u"complete"_s);
        m.applyEdit(u"a"_s, QJsonObject{{u"content"_s, u"Hello!"_s}, {u"edited_at"_s, 200.0}});
        QCOMPARE(m.data(m.index(0), MessageModel::ContentRole).toString(), u"Hello!"_s);
        QVERIFY(m.data(m.index(0), MessageModel::EditedRole).toBool());
        m.setReactions(u"a"_s, QJsonObject{{u"+1"_s, QJsonArray{u"x"_s, u"y"_s}}});
        QCOMPARE(m.data(m.index(0), MessageModel::ReactionsTextRole).toString(), u"+1 2"_s);
        m.markDeleted(u"a"_s, 300);
        QVERIFY(m.data(m.index(0), MessageModel::DeletedRole).toBool());
        // unknown ids are ignored
        m.appendDelta(u"zzz"_s, u"x"_s);
        QCOMPARE(m.count(), 1);
    }
    void model_prepends_history_and_places_the_new_divider()
    {
        MessageModel m;
        m.setAll({msg(u"c"_s, u"x"_s, 300), msg(u"d"_s, u"x"_s, 400), msg(u"e"_s, u"x"_s, 500)}, 2);
        QVERIFY(m.data(m.index(1), MessageModel::DividerRole).toBool()); // the first of the 2 unread
        QVERIFY(!m.data(m.index(0), MessageModel::DividerRole).toBool());
        m.prepend({msg(u"b"_s, u"x"_s, 200), msg(u"a"_s, u"x"_s, 100), msg(u"c"_s, u"x"_s, 300)});
        QCOMPARE(ids(m), (QStringList{u"a"_s, u"b"_s, u"c"_s, u"d"_s, u"e"_s}));
        QCOMPARE(m.oldestServerTime(), 100.0);
    }
    void model_block_kinds()
    {
        const QJsonArray blocks{QJsonObject{{u"kind"_s, u"thinking"_s}, {u"text"_s, u"hm"_s}},
                                QJsonObject{{u"kind"_s, u"tool_call"_s}, {u"name"_s, u"search"_s}, {u"status"_s, u"running"_s}},
                                QJsonObject{{u"kind"_s, u"text"_s}, {u"text"_s, u"done"_s}},
                                QJsonObject{{u"kind"_s, u"question"_s}, {u"text"_s, u"ok?"_s}, {u"options"_s, QJsonArray{u"y"_s, u"n"_s}}},
                                QJsonObject{{u"kind"_s, u"decision"_s}, {u"decision_id"_s, u"d1"_s}},
                                QJsonObject{{u"kind"_s, u"hologram"_s}}};
        const QVariantList p = MessageModel::partsFor({}, blocks);
        QStringList kinds;
        for (const QVariant &v : p)
            kinds << v.toMap().value(u"kind"_s).toString();
        QCOMPARE(kinds, (QStringList{u"thinking"_s, u"tool"_s, u"html"_s, u"status"_s, u"decision"_s, u"unknown"_s}));
        QCOMPARE(p.last().toMap().value(u"name"_s).toString(), u"hologram"_s);
    }

    // ---- the engine against the fake controller ----------------------------------------------
    void initTestCase()
    {
        m_fake = new FakeHost;
        QVERIFY2(m_fake->ok(), "fake chat controller did not start");
        m_port = m_fake->url().port();
        m_host = new AppHost(m_fake->url(), u"alex"_s, u"hunter2-secret-pw"_s, u"my.taos.messages"_s);
        QVERIFY(m_host->waitSignedIn());
    }
    void cleanupTestCase()
    {
        delete m_host;
        delete m_fake;
    }
    void init() { control({{u"seed"_s, true}}); }

    void engine_lists_sections_unread_and_presence()
    {
        ChatEngine e;
        e.setRunning(true);
        auto named = [&](const QString &n) {
            for (const ChannelRow &r : e.channelModel()->rows())
                if (!r.header && r.name == n) return true;
            return false;
        };
        QVERIFY(waitFor([&] { return e.channelsLoaded() && e.currentUserId() == u"usr_1"_s && e.channelModel()->rowCount() > 8 && named(u"Alpha"_s) && named(u"Beta"_s) && e.totalUnread() == 3; }));
        QStringList sections, names;
        for (const ChannelRow &r : e.channelModel()->rows())
            (r.header ? sections : names) << (r.header ? r.section : r.name);
        QCOMPARE(sections, (QStringList{u"Channels"_s, u"Agents-DMs"_s, u"Direct Messages"_s}));
        // within a section: newest activity first
        QCOMPARE(names.mid(0, 2), (QStringList{u"general"_s, u"build-room"_s}));
        // agent DMs use the agent's display name and carry presence
        const ChannelRow *alpha = nullptr, *beta = nullptr;
        for (const ChannelRow &r : e.channelModel()->rows()) {
            if (r.id == u"ch-alpha"_s) alpha = &r;
            if (r.id == u"ch-beta"_s) beta = &r;
        }
        QVERIFY(alpha && beta);
        QCOMPARE(alpha->name, u"Alpha"_s);
        QCOMPARE(alpha->presence, u"live"_s);
        QCOMPARE(beta->presence, u"idle"_s);
        QCOMPARE(alpha->unread, 1);
        QCOMPARE(e.totalUnread(), 3);
        // a2a channels stay under Direct Messages
        QVERIFY(names.contains(u"a2a-alpha-beta"_s));
        // collapsing a section keeps channels that are unread
        e.toggleSection(u"Channels"_s);
        QStringList left;
        for (const ChannelRow &r : e.channelModel()->rows())
            if (!r.header && r.type != u"dm"_s) left << r.id;
        QCOMPARE(left, (QStringList{u"ch-general"_s})); // unread 2; build-room read, hidden
    }
    void engine_opens_conversation_marks_read_and_joins()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-general"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 6; }));
        QCOMPARE(ids(*e.messageModel()).first(), u"m001"_s); // ascending
        QCOMPARE(e.selectedName(), u"general"_s);
        QCOMPARE(e.selectedTopic(), u"Release week"_s);
        // the 2 unread: the divider sits on the 5th message
        QVERIFY(e.messageModel()->data(e.messageModel()->index(4), MessageModel::DividerRole).toBool());
        QVERIFY(waitFor([&] { return countPosts({}, u"/api/chat/channels/ch-general/mark-read"_s, u"path"_s) >= 1; }));
        QVERIFY(waitFor([&] { return e.totalUnread() == 1; }));
        QVERIFY(waitFor([&] { return countPosts(u"ch-general"_s, u"join"_s) >= 1; }));
        QVERIFY(waitFor([&] { return e.live(); }));
    }
    void engine_pages_back_in_history()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-build"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 50; }));
        QVERIFY(e.hasOlder());
        e.loadOlder();
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 100; }));
        e.loadOlder();
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 131; })); // 130 history + 1
        QVERIFY(!e.hasOlder());
        QVERIFY(std::is_sorted(e.messageModel()->msgs().begin(), e.messageModel()->msgs().end(),
                               [](const Msg &a, const Msg &b) { return a.createdAt < b.createdAt; }));
    }
    void engine_send_is_optimistic_and_not_duplicated_by_the_broadcast()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-alpha"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 4 && e.live(); }));
        QVERIFY(waitFor([&] { return countPosts(u"ch-alpha"_s, u"join"_s) >= 1; }));
        e.send(u"  hello there  "_s);
        QCOMPARE(e.messageModel()->count(), 5);
        QCOMPARE(e.messageModel()->data(e.messageModel()->index(4), MessageModel::StateRole).toString(), u"sending"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 5 && e.messageModel()->msgs().last().localId.isEmpty(); }));
        QTest::qWait(300); // the broadcast must not add a second copy
        QCOMPARE(e.messageModel()->count(), 5);
        QCOMPARE(e.messageModel()->msgs().last().content, u"hello there"_s);
        QVERIFY(e.messageModel()->msgs().last().own);
        // the exact POST body the web app sends
        bool found = false;
        for (const QJsonValue &v : posts()) {
            const QJsonObject o = v.toObject();
            if (o.value(u"content"_s).toString() == u"hello there"_s) {
                found = true;
                QCOMPARE(o.value(u"author_id"_s).toString(), u"user"_s);
                QCOMPARE(o.value(u"author_type"_s).toString(), u"user"_s);
                QCOMPARE(o.value(u"content_type"_s).toString(), u"text"_s);
                QCOMPARE(o.value(u"channel_id"_s).toString(), u"ch-alpha"_s);
            }
        }
        QVERIFY(found);
        // an agent DM arms the stall watch, a delta from the agent keeps it quiet
        QVERIFY(e.stallText().isEmpty());
    }
    void engine_failed_send_keeps_the_row_and_retry_works()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-general"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 6; }));
        control({{u"fail_send"_s, u"/clear"_s}});
        e.send(u"/clear"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().state == u"failed"_s; }));
        QCOMPARE(e.messageModel()->msgs().last().error, u"address a member first"_s);
        QCOMPARE(e.sendError(), u"address a member first"_s);
        control({{u"fail_send"_s, u"nothing"_s}});
        e.retry(e.messageModel()->msgs().last().localId);
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().localId.isEmpty() && e.messageModel()->count() == 7; }));
        QVERIFY(e.sendError().isEmpty());
    }
    void engine_help_is_handled_by_the_server()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-general"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 6 && e.live(); }));
        QVERIFY(waitFor([&] { return countPosts(u"ch-general"_s, u"join"_s) >= 1; }));
        e.send(u"/help"_s);
        // no echo of "/help" itself, only the server's system message
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().authorType == u"system"_s; }));
        QTest::qWait(200);
        int helps = 0;
        for (const Msg &m : e.messageModel()->msgs())
            helps += m.content == u"/help"_s;
        QCOMPARE(helps, 0);
        QCOMPARE(e.messageModel()->count(), 7);
    }
    void engine_live_frames_stream_state_typing_and_thinking()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-alpha"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 4 && e.live(); }));
        QVERIFY(waitFor([&] { return countPosts(u"ch-alpha"_s, u"join"_s) >= 1; }));
        auto push = [&](const QJsonObject &f) { control({{u"frame"_s, f}, {u"channel_id"_s, u"ch-alpha"_s}}); };
        push({{u"type"_s, u"message"_s}, {u"id"_s, u"s1"_s}, {u"channel_id"_s, u"ch-alpha"_s}, {u"author_id"_s, u"alpha"_s},
              {u"author_type"_s, u"agent"_s}, {u"content"_s, u""_s}, {u"state"_s, u"pending"_s},
              {u"created_at"_s, double(QDateTime::currentSecsSinceEpoch())}});
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 5; }));
        QSignalSpy appended(&e, &ChatEngine::messageAppended);
        push({{u"type"_s, u"message_delta"_s}, {u"message_id"_s, u"s1"_s}, {u"delta"_s, u"Hel"_s}});
        push({{u"type"_s, u"message_delta"_s}, {u"message_id"_s, u"s1"_s}, {u"delta"_s, u"lo"_s}});
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().content == u"Hello"_s; }));
        QCOMPARE(e.messageModel()->msgs().last().state, u"streaming"_s);
        push({{u"type"_s, u"message_state"_s}, {u"message_id"_s, u"s1"_s}, {u"state"_s, u"complete"_s}});
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().state == u"complete"_s; }));
        // typing: a human (not me) and an agent thinking, the line clears when the agent ends
        push({{u"type"_s, u"typing"_s}, {u"kind"_s, u"human"_s}, {u"slug"_s, u"ruth"_s}});
        push({{u"type"_s, u"thinking"_s}, {u"slug"_s, u"alpha"_s}, {u"state"_s, u"start"_s}, {u"phase"_s, u"tool"_s}});
        QVERIFY(waitFor([&] { return e.typingText().contains(u"ruth is typing"_s) && e.typingText().contains(u"@alpha is using a tool"_s); }));
        const ChannelRow *alpha = nullptr;
        for (const ChannelRow &r : e.channelModel()->rows())
            if (r.id == u"ch-alpha"_s) alpha = &r;
        QCOMPARE(alpha->presence, u"working"_s);
        push({{u"type"_s, u"thinking"_s}, {u"slug"_s, u"alpha"_s}, {u"state"_s, u"end"_s}});
        QVERIFY(waitFor([&] { return !e.typingText().contains(u"alpha"_s); }));
        // edits, deletes and reactions
        push({{u"type"_s, u"message_edit"_s}, {u"message_id"_s, u"s1"_s}, {u"content"_s, u"Hello!"_s}, {u"edited_at"_s, 1.76e9}});
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().content == u"Hello!"_s; }));
        push({{u"type"_s, u"message_delete"_s}, {u"message_id"_s, u"s1"_s}, {u"channel_id"_s, u"ch-alpha"_s}});
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().deleted; }));
        // a frame this version does not know, and a broken one, change nothing
        push({{u"type"_s, u"from_the_future"_s}});
        QTest::qWait(150);
        QCOMPARE(e.messageModel()->count(), 5);
    }
    void engine_survives_a_dropped_socket_and_rejoins()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-alpha"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 4 && e.live(); }));
        QVERIFY(waitFor([&] { return countPosts(u"ch-alpha"_s, u"join"_s) >= 1; }));
        const int joinsBefore = countPosts(u"ch-alpha"_s, u"join"_s);
        control({{u"drop"_s, true}});
        // reconnects by itself (backoff 50 to 200 ms in the harness), re-joins, and refetches
        QVERIFY(waitFor([&] { return countPosts(u"ch-alpha"_s, u"join"_s) > joinsBefore; }, 15000));
        QVERIFY(waitFor([&] { return e.live(); }, 15000));
        control({{u"frame"_s, QJsonObject{{u"type"_s, u"message"_s}, {u"id"_s, u"late"_s}, {u"channel_id"_s, u"ch-alpha"_s},
                 {u"author_id"_s, u"alpha"_s}, {u"author_type"_s, u"agent"_s}, {u"content"_s, u"back"_s},
                 {u"created_at"_s, double(QDateTime::currentSecsSinceEpoch())}}}, {u"channel_id"_s, u"ch-alpha"_s}});
        QVERIFY(waitFor([&] { return e.messageModel()->msgs().last().id == u"late"_s; }));
    }
    void engine_open_agent_and_arguments()
    {
        ChatEngine e;
        // before anything has loaded: remembered, applied once the list is there
        e.applyArguments({u"--agent"_s, u"beta"_s});
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.selectedId() == u"ch-beta"_s; }));
        e.applyArguments({u"--channel=ch-general"_s});
        QCOMPARE(e.selectedId(), u"ch-general"_s);
        e.applyArguments({u"--channel"_s, u"ch-jo"_s});
        QCOMPARE(e.selectedId(), u"ch-jo"_s);
        e.openAgent(u"nobody"_s);
        QVERIFY(!e.messagesError().isEmpty());
        QCOMPARE(e.selectedId(), u"ch-jo"_s);
    }
    void engine_errors_are_reported_not_hidden()
    {
        ChatEngine e;
        m_fake->config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/chat/channels"_s}, {u"status"_s, 500},
                                                {u"times"_s, 100}, {u"message"_s, u"database is locked"_s}}}});
        e.setRunning(true);
        QVERIFY(waitFor([&] { return !e.channelsError().isEmpty(); }));
        QVERIFY(!e.channelsLoaded());
        QVERIFY(e.channelsError().contains(u"database is locked"_s) || !e.channelsError().isEmpty());
        m_fake->config({{u"fail"_s, QJsonValue()}});
        e.refresh();
        QVERIFY(waitFor([&] { return e.channelsLoaded() && e.channelsError().isEmpty(); }));
        // an unknown conversation gives an error state, not an empty list
        e.openChannel(u"ch-missing"_s);
        QVERIFY(waitFor([&] { return !e.messagesError().isEmpty(); }));
        QCOMPARE(e.messageModel()->count(), 0);
    }
    void engine_draft_and_mentions()
    {
        ChatEngine e;
        e.setRunning(true);
        QVERIFY(waitFor([&] { return e.channelsLoaded(); }));
        e.openChannel(u"ch-general"_s);
        QVERIFY(waitFor([&] { return e.messageModel()->count() == 6; }));
        QCOMPARE(e.mentionCandidates(u"ping @a"_s, 7), (QStringList{u"alpha"_s, u"all"_s, u"beta"_s}));
        QCOMPARE(e.insertMention(u"ping @al and more"_s, 8, u"alpha"_s), u"ping @alpha  and more"_s);
        e.setDraft(u"ch-general"_s, u"half a thought"_s);
        e.openChannel(u"ch-alpha"_s);
        QCOMPARE(e.draft(u"ch-general"_s), u"half a thought"_s);
        QCOMPARE(e.searchChannels(u"gn"_s).size(), 1);
        QVERIFY(e.searchChannels(u""_s).size() >= 6);
    }

    // ---- the QML app ---------------------------------------------------------------------------
    void ui_loads_lists_conversation_and_sends()
    {
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.messages/Main.qml"_s);
        QVERIFY(w);
        w->requestActivate();
        QObject *eng = w->findChild<QObject *>(u"engine"_s);
        QVERIFY(eng);
        QVERIFY(waitFor([&] { return eng->property("channelsLoaded").toBool(); }));
        auto *list = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"channelListView"_s; });
        QVERIFY(list);
        QVERIFY(waitFor([&] { return list->property("count").toInt() >= 9; }));
        // a channel row has an accessible name that says the unread count
        auto *row = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"channel_ch-general"_s; });
        QVERIFY(row);
        QMetaObject::invokeMethod(row, "clicked");
        QVERIFY(waitFor([&] { return eng->property("selectedId").toString() == u"ch-general"_s; }));
        auto *tr = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"transcript"_s; });
        QVERIFY(tr);
        QVERIFY(waitFor([&] { return tr->property("count").toInt() == 6; }));
        // type and send through the composer's own key handling
        auto *input = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"composerInput"_s; });
        QVERIFY(input);
        input->forceActiveFocus();
        QVERIFY(waitFor([&] { return input->hasActiveFocus(); }));
        typeText(w, u"from the ui"_s);
        QTest::keyClick(w, Qt::Key_Return);
        QVERIFY(waitFor([&] { return tr->property("count").toInt() == 7; }));
        QTest::qWait(400); // the POST answer and the broadcast settle on ONE row
        QVERIFY(input->property("text").toString().isEmpty());
        // shift+enter inserts a newline and does not send
        typeText(w, u"a"_s);
        QTest::keyClick(w, Qt::Key_Return, Qt::ShiftModifier);
        typeText(w, u"b"_s);
        QCOMPARE(input->property("text").toString(), u"a\nb"_s);
        QCOMPARE(tr->property("count").toInt(), 7);
    }
    void ui_quick_switcher_opens_with_ctrl_k()
    {
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.messages/Main.qml"_s);
        QVERIFY(w);
        w->requestActivate();
        QObject *eng = w->findChild<QObject *>(u"engine"_s);
        QVERIFY(waitFor([&] { return eng->property("channelsLoaded").toBool() && w->isActive(); }));
        QTest::keyClick(w, Qt::Key_K, Qt::ControlModifier);
        auto *sw = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"switcher"_s; });
        QVERIFY(sw);
        QVERIFY(waitFor([&] { return sw->isVisible(); }));
        typeText(w, u"alp"_s);
        QTest::keyClick(w, Qt::Key_Return);
        QVERIFY(waitFor([&] { return eng->property("selectedId").toString() == u"ch-alpha"_s; }));
        QVERIFY(!sw->isVisible());
    }
    void ui_empty_and_error_states()
    {
        control({{u"empty"_s, true}});
        QQuickWindow *w = m_host->load(u"qrc:/my.taos.messages/Main.qml"_s);
        QVERIFY(w);
        QObject *eng = w->findChild<QObject *>(u"engine"_s);
        QVERIFY(waitFor([&] { return eng->property("channelsLoaded").toBool(); }));
        auto *empty = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"channelsEmpty"_s; });
        QVERIFY(empty && waitFor([&] { return empty->isVisible(); }));
        m_host->unload();
        control({{u"seed"_s, true}});
        m_fake->config({{u"fail"_s, QJsonObject{{u"path"_s, u"/api/chat/channels"_s}, {u"status"_s, 500}, {u"times"_s, 100}}}});
        w = m_host->load(u"qrc:/my.taos.messages/Main.qml"_s);
        QVERIFY(w);
        auto *err = findItem(w->contentItem(), [](QQuickItem *i) { return i->objectName() == u"channelsError"_s; });
        QVERIFY(err && waitFor([&] { return err->isVisible(); }));
        m_fake->config({{u"fail"_s, QJsonValue()}});
    }
};

QTEST_MAIN(TstMessages)
#include "tst_messages.moc"
