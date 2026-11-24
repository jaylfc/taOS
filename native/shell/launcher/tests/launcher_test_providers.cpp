#include <QtTest>

#include "providers_more.h"
#include "runner.h"

using namespace taos::search;

static const CancelToken kNoCancel;

static QString fixtures() { return QStringLiteral(TAOS_LAUNCHER_FIXTURES); }
static QByteArray windowsJson() { QFile f(fixtures() + "/windows.json"); f.open(QIODevice::ReadOnly); return f.readAll(); }
static QStringList titles(const QList<Result> &r) { QStringList l; for (const Result &x : r) l << x.title; return l; }

class TestProviders : public QObject {
    Q_OBJECT
    QTemporaryDir m_tmp;
    int m_n = 0;
    std::shared_ptr<Frecency> frec() { return std::make_shared<Frecency>(m_tmp.path() + QString("/f%1.json").arg(m_n++)); }
    std::shared_ptr<AppsProvider> apps(std::shared_ptr<Frecency> f = {}) {
        return std::make_shared<AppsProvider>(f ? f : frec(), QStringList{ fixtures() + "/applications" }, "C", QStringList{});
    }
private slots:
    // ---- apps and ranking
    void appsFuzzyRanking() {
        auto a = apps();
        QCOMPARE(titles(a->query("fire", kNoCancel)).value(0), QString("Firefox"));
        QCOMPARE(titles(a->query("firefox", kNoCancel)).value(0), QString("Firefox"));
        QCOMPARE(titles(a->query("ffx", kNoCancel)).value(0), QString("Firefox"));
        QCOMPARE(titles(a->query("browser", kNoCancel)).value(0), QString("Firefox"));   // generic name / keyword
        QCOMPARE(titles(a->query("photos", kNoCancel)).value(0), QString("Image Viewer"));   // keyword
        QVERIFY(a->query("zzzzqq", kNoCancel).isEmpty());
        QVERIFY(!titles(a->query("hidden", kNoCancel)).contains("Hidden Thing"));   // NoDisplay never offered
    }
    void appsActionsIncludeDesktopActions() {
        auto a = apps();
        const Result r = a->query("firefox", kNoCancel).first();
        QStringList labels;
        for (const Action &x : r.actions) labels << x.label;
        QVERIFY(labels.contains("Open"));
        QVERIFY(labels.contains("New Window"));
        QVERIFY(labels.contains("New Private Window"));
        QCOMPARE(r.actions.first().verb, QString("launch"));
        QCOMPARE(r.actions.first().args, QStringList({ "firefox" }));
        const Result h = a->query("htop", kNoCancel).first();
        QCOMPARE(h.actions.first().data, QString("terminal"));
    }
    void frecencyLiftsUsedApps() {
        auto f = frec();
        auto a = apps(f);
        // "t" matches several apps; use the one with no frecency first, then make Music popular
        const QString before = a->query("m", kNoCancel).first().title;
        for (int i = 0; i < 6; ++i) f->record("app:taos-music.desktop");
        QCOMPARE(a->query("m", kNoCancel).first().title, QString("Music"));
        Q_UNUSED(before);
    }
    void emptyQuerySuggestsFrequent() {
        auto f = frec();
        auto a = apps(f);
        QVERIFY(a->query("", kNoCancel).isEmpty());   // nothing used yet
        f->record("app:foot.desktop"); f->record("app:foot.desktop"); f->record("app:firefox.desktop");
        QCOMPARE(titles(a->query("", kNoCancel)), QStringList({ "Terminal", "Firefox" }));
    }
    void cancelledAppsQueryReturnsNothing() {
        auto a = apps();
        CancelToken c; c.cancel();
        QVERIFY(a->query("fire", c).isEmpty());
    }
    // ---- windows
    void windowsParseAndFilter() {
        const auto icon = [](const QString &id) { return id == "firefox" ? QString("firefox") : QString(); };
        const QList<Result> all = WindowsProvider::parse(windowsJson(), "", icon);
        QStringList ids; for (const Result &r : all) ids << r.id;
        QVERIFY(!ids.contains("window:11"));   // the focused window is not a suggestion
        QCOMPARE(ids.value(0), QString("window:14"));
        const QList<Result> q = WindowsProvider::parse(windowsJson(), "notes", icon);
        QCOMPARE(q.size(), 1);
        QCOMPARE(q[0].actions[0].verb, QString("focus-window"));
        QCOMPARE(q[0].actions[0].data, QString("21"));
        QVERIFY(q[0].subtitle.contains("Minimised"));
        QCOMPARE(WindowsProvider::parse(windowsJson(), "firefox", icon).first().icon, QString("firefox"));   // by app id
    }
    void windowsBadJsonIsQuiet() {
        QVERIFY(WindowsProvider::parse("not json", "x", {}).isEmpty());
        QVERIFY(WindowsProvider::parse("{\"a\":1}", "x", {}).isEmpty());
        QVERIFY(WindowsProvider::parse("[1,2,null,{\"title\":\"no id\"}]", "no", {}).isEmpty());
    }
    void windowsDisabledWithoutCli() {
        WindowsProvider p;   // default fetch: needs taos-window on PATH
        const bool has = !QStandardPaths::findExecutable("taos-window").isEmpty();
        QCOMPARE(p.available(), has);
        WindowsProvider injected([](bool *ok) { *ok = true; return QByteArray("[]"); });
        QVERIFY(injected.available());
    }
    void windowsFetchFailureYieldsNothing() {
        WindowsProvider p([](bool *ok) { *ok = false; return QByteArray(); });
        QVERIFY(p.query("x", kNoCancel).isEmpty());
    }
    void windowsListIsCachedBriefly() {
        int calls = 0;
        WindowsProvider p([&](bool *ok) { ++calls; *ok = true; return windowsJson(); });
        p.query("a", kNoCancel); p.query("ab", kNoCancel); p.query("abc", kNoCancel);
        QCOMPARE(calls, 1);
        p.prepare();   // the launcher opens again: refetch
        p.query("a", kNoCancel);
        QCOMPARE(calls, 2);
    }
    // ---- commands: each only when its CLI exists
    void commandsOnlyWhenCliExists() {
        QSet<QString> have;
        CommandsProvider none([&](const QString &n) { return have.contains(n); });
        QVERIFY(none.offered().isEmpty());
        have = { "systemctl" };
        CommandsProvider sys([&](const QString &n) { return have.contains(n); });
        QStringList t = titles(sys.offered());
        QVERIFY(t.contains("Restart") && t.contains("Shut down") && t.contains("Sleep"));
        QVERIFY(!t.contains("Log out") && !t.contains("Dark mode") && !t.contains("Tiling"));
        have = { "taos-theme" };
        t = titles(CommandsProvider([&](const QString &n) { return have.contains(n); }).offered());
        QVERIFY(t.contains("Dark mode") && t.contains("Light mode") && t.contains("Theme: Graphite Ice"));
        QVERIFY(!t.contains("Restart"));
        have = { "taos-dock" };
        t = titles(CommandsProvider([&](const QString &n) { return have.contains(n); }).offered());
        QVERIFY(t.contains("Dock: Left") && t.contains("Dock: Hide automatically"));
        have = { "taos-display" };
        t = titles(CommandsProvider([&](const QString &n) { return have.contains(n); }).offered());
        QVERIFY(t.contains("Display scale: 150%"));
        have = { "taos-window" };
        t = titles(CommandsProvider([&](const QString &n) { return have.contains(n); }).offered());
        QVERIFY(t.contains("Tiling") && t.contains("Desktop"));
    }
    void lockFallsBackToLoginctl() {
        const auto lock = [](QSet<QString> have) {
            CommandsProvider p([have](const QString &n) { return have.contains(n); });
            for (const Result &r : p.offered()) if (r.id == "cmd:lock") return r.actions[0].args;
            return QStringList();
        };
        QCOMPARE(lock({ "swaylock", "loginctl" }), QStringList({ "swaylock", "-f" }));
        QCOMPARE(lock({ "loginctl" }), QStringList({ "loginctl", "lock-session" }));
        QVERIFY(lock({}).isEmpty());
    }
    void powerActionsNeedConfirmation() {
        CommandsProvider p([](const QString &) { return true; });
        for (const Result &r : p.offered()) {
            const bool power = r.id == "cmd:logout" || r.id == "cmd:reboot" || r.id == "cmd:poweroff";
            QCOMPARE(r.actions[0].confirm, power);
        }
        QCOMPARE(p.query("shut", kNoCancel).first().id, QString("cmd:poweroff"));
        QCOMPARE(p.query("dark", kNoCancel).first().id, QString("cmd:theme-dark"));
        QCOMPARE(p.query("scale", kNoCancel).first().actions[0].verb, QString("display-scale"));
    }
    // ---- calculator provider
    void calcProvider() {
        CalcProvider p;
        const QList<Result> r = p.query("12*(3+4)", kNoCancel);
        QCOMPARE(r.size(), 1);
        QCOMPARE(r[0].title, QString("84"));
        QCOMPARE(r[0].actions[0].verb, QString("copy"));
        QCOMPARE(r[0].actions[0].data, QString("84"));
        QVERIFY(p.query("42", kNoCancel).isEmpty());
        QVERIFY(p.query("firefox", kNoCancel).isEmpty());
        QVERIFY(p.query("1/0", kNoCancel).isEmpty());
        QVERIFY(p.query("", kNoCancel).isEmpty());
    }
    // ---- files
    void filesFindsByNameAndSkipsHidden() {
        QTemporaryDir home;
        QDir d(home.path());
        d.mkpath("Documents/Reports"); d.mkpath(".cache/stuff"); d.mkpath("node_modules/x");
        for (const char *f : { "Documents/Reports/budget-2026.ods", "Documents/notes.txt", ".cache/stuff/budget-secret.txt", "node_modules/x/budget.js" }) {
            QFile fh(home.path() + "/" + f); QVERIFY(fh.open(QIODevice::WriteOnly)); fh.write("x");
        }
        auto idx = std::make_shared<FileIndex>(QStringList{ home.path() }, home.path() + "/cache/files.txt");
        QVERIFY(idx->rebuild(kNoCancel));
        FilesProvider p(idx);
        const QList<Result> r = p.query("budget", kNoCancel);
        QCOMPARE(r.size(), 1);
        QCOMPARE(r[0].title, QString("budget-2026.ods"));
        QCOMPARE(r[0].actions[0].verb, QString("open"));
        QCOMPARE(r[0].actions[0].data, home.path() + "/Documents/Reports/budget-2026.ods");
        QVERIFY(p.query("b", kNoCancel).isEmpty());   // one letter is not searched
        QCOMPARE(p.query("reports", kNoCancel).first().icon, QString("folder"));
        // the cache survives a restart
        FileIndex again(QStringList{ home.path() }, home.path() + "/cache/files.txt");
        again.loadCache();
        QVERIFY(again.snapshot()->size() > 0);
        QCOMPARE(again.snapshot()->size(), idx->snapshot()->size());
    }
    void fileWalkIsBoundedAndCancellable() {
        QTemporaryDir home;
        QDir d(home.path());
        d.mkpath("a/b/c/d/e/f/g");
        for (int i = 0; i < 50; ++i) { QFile f(QString("%1/a/file%2.txt").arg(home.path()).arg(i)); f.open(QIODevice::WriteOnly); }
        FileIndex::Limits lim; lim.maxEntries = 10;
        FileIndex capped({ home.path() }, home.path() + "/c1.txt", lim);
        capped.rebuild(kNoCancel);
        QVERIFY(capped.snapshot()->size() <= 10);
        FileIndex::Limits shallow; shallow.maxDepth = 2;
        FileIndex sh({ home.path() }, home.path() + "/c2.txt", shallow);
        sh.rebuild(kNoCancel);
        for (const FileEntry &e : *sh.snapshot()) QVERIFY2(!e.path.endsWith("/c") && !e.path.endsWith("/d"), qPrintable(e.path));
        CancelToken c; c.cancel();
        FileIndex cancelled({ home.path() }, home.path() + "/c3.txt");
        QVERIFY(!cancelled.rebuild(c));
        QCOMPARE(cancelled.snapshot()->size(), 0);
    }
    void fileIndexSurvivesSymlinkLoop() {
        QTemporaryDir home;
        QDir(home.path()).mkpath("a");
        QVERIFY(QFile::link(home.path(), home.path() + "/a/loop"));
        FileIndex idx({ home.path() }, home.path() + "/c.txt");
        QVERIFY(idx.rebuild(kNoCancel));
        QVERIFY(idx.snapshot()->size() < 100);
    }
    void corruptFileCacheIsIgnored() {
        QTemporaryDir d;
        QFile f(d.path() + "/c.txt"); f.open(QIODevice::WriteOnly); f.write("garbage\nmore"); f.close();
        FileIndex idx({ d.path() }, d.path() + "/c.txt");
        idx.loadCache();
        QCOMPARE(idx.snapshot()->size(), 0);
    }
    // ---- settings entries
    void settingsAreStubsUntilTheAppExists() {
        SettingsProvider none([](const QString &) { return false; });
        const QList<Result> r = none.query("display", kNoCancel);
        QVERIFY(!r.isEmpty());
        QCOMPARE(r[0].title, QString("Displays"));
        QVERIFY(r[0].actions[0].stub);
        QVERIFY(r[0].subtitle.contains("not available yet"));
        SettingsProvider app([](const QString &n) { return n == "taos-settings"; });
        const Result a = app.query("sound", kNoCancel).first();
        QVERIFY(!a.actions[0].stub);
        QCOMPARE(a.actions[0].args, QStringList({ "taos-settings", "--section", "sound" }));
        const Result p = app.query("battery", kNoCancel).first();
        QVERIFY(p.actions[0].stub);
        QCOMPARE(app.query("bluetooth", kNoCancel).first().actions[0].args, QStringList({ "taos-settings", "--section", "network" }));
        QVERIFY(SettingsProvider().query("", kNoCancel).isEmpty());
    }
    // ---- Ask taOS
    void askAlwaysOffersARow() {
        auto cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/none.json");
        AskProvider none(cfg, [] { return QList<DesktopEntry>(); });
        QVERIFY(none.query("", kNoCancel).isEmpty());
        Result r = none.query("what is the weather", kNoCancel).first();
        QCOMPARE(r.title, QString("Ask taOS: what is the weather"));
        QCOMPARE(r.kind, QString("ask"));
        QVERIFY(r.actions[0].stub);
        QCOMPARE(r.actions[0].label, QString("Agent chat is not set up yet"));
        // a Messages desktop entry exists: open it with the question
        DesktopEntry e; e.id = "org.taos.Messages.desktop"; e.name = "Messages"; e.exec = "taos-messages %U"; e.isApplication = true;
        AskProvider chat(cfg, [e] { return QList<DesktopEntry>{ e }; });
        r = chat.query("hello", kNoCancel).first();
        QCOMPARE(r.actions[0].verb, QString("ask-open"));
        QCOMPARE(r.actions[0].args, QStringList({ "taos-messages" }));
        QCOMPARE(r.actions[0].data, QString("hello"));
        // a configured command wins
        cfg->setAskCommand({ "my-agent", "--say", "{query}" });
        r = chat.query("hello world", kNoCancel).first();
        QCOMPARE(r.actions[0].verb, QString("exec"));
        QCOMPARE(r.actions[0].args, QStringList({ "my-agent", "--say", "hello world" }));
    }
    // ---- mentions
    void mentionAutocomplete() {
        auto dir = std::make_shared<FakeDirectory>(QList<Agent>{ { "a1", "Parker", "online" }, { "a2", "Pixel Pete", "offline" }, { "a3", "Quill", "" } });
        auto cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/none.json");
        MentionProvider p(dir, cfg, false);
        QCOMPARE(p.query("hello", kNoCancel).size(), 0);   // not a mention
        QCOMPARE(p.query("@", kNoCancel).size(), 3);
        QList<Result> r = p.query("@par", kNoCancel);
        QCOMPARE(r.first().title, QString("@parker"));
        QCOMPARE(r.first().actions[0].verb, QString("complete"));
        QCOMPARE(r.first().actions[0].data, QString("@parker "));
        QCOMPARE(p.query("@pixel-p", kNoCancel).first().title, QString("@pixel-pete"));
        QCOMPARE(p.query("@parker @q", kNoCancel).first().actions[0].data, QString("@parker @quill "));   // earlier mention kept
        QVERIFY(p.query("@zzzz", kNoCancel).isEmpty());
    }
    void mentionSendDefaultsAndOverride() {
        auto dir = std::make_shared<FakeDirectory>(QList<Agent>{ { "a1", "Parker", "online" }, { "a2", "Quill", "online" } });
        auto cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/none.json");
        MentionProvider p(dir, cfg, false);
        Result r = p.query("@parker  can you summarise the inbox", kNoCancel).first();
        QCOMPARE(r.actions[0].verb, QString("send-mention"));
        QCOMPARE(r.actions[0].id, QString("send"));
        QCOMPARE(r.actions[0].shownHint(), QString("Send"));
        QCOMPARE(r.actions[1].id, QString("send-open"));
        QCOMPARE(r.actions[1].label, QString("Send and open chat"));
        QCOMPARE(r.actions[0].args, QStringList({ "a1" }));
        QCOMPARE(r.actions[0].data, QString("can you summarise the inbox"));
        cfg->setMentionOpensChat(true);
        r = p.query("@parker hi", kNoCancel).first();
        QCOMPARE(r.actions[0].id, QString("send-open"));
        QCOMPARE(r.actions[0].shownHint(), QString("Send and open"));
        QCOMPARE(r.actions[1].label, QString("Send only"));
    }
    void mentionSeveralAgents() {
        auto dir = std::make_shared<FakeDirectory>(QList<Agent>{ { "a1", "Parker", "online" }, { "a2", "Quill", "online" } });
        auto cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/none.json");
        Result one = MentionProvider(dir, cfg, false).query("@parker @quill hi", kNoCancel).first();
        QCOMPARE(one.actions[0].args, QStringList({ "a1" }));              // the chat takes one: first only
        QVERIFY(one.subtitle.contains("only @parker"));                     // and it says so
        Result many = MentionProvider(dir, cfg, true).query("@parker @quill hi", kNoCancel).first();
        QCOMPARE(many.actions[0].args, QStringList({ "a1", "a2" }));
        QCOMPARE(MentionProvider(dir, cfg, true).query("@parker @parker hi", kNoCancel).first().actions[0].args, QStringList({ "a1" }));   // no duplicates
    }
    void mentionEdgeCases() {
        auto dir = std::make_shared<FakeDirectory>(QList<Agent>{ { "a1", "Parker", "online" } });
        auto cfg = std::make_shared<LauncherConfig>(m_tmp.path() + "/none.json");
        MentionProvider p(dir, cfg, false);
        Result r = p.query("@nobody hello", kNoCancel).first();
        QVERIFY(r.actions[0].stub);
        QVERIFY(r.title.contains("nobody"));
        r = p.query("@parker ", kNoCancel).first();   // named but no message yet
        QVERIFY(r.actions[0].stub);
        QCOMPARE(r.actions[0].shownHint(), QString("Type a message"));
        // no agents at all: one honest row, never a fake list
        MentionProvider empty(std::make_shared<UnavailableDirectory>(), cfg, false);
        r = empty.query("@", kNoCancel).first();
        QVERIFY(r.actions[0].stub);
        QVERIFY(r.subtitle.contains("not connected"));
        QCOMPARE(MentionProvider::handleOf("Pixel Pete!"), QString("pixel-pete"));
    }
    void configFile() {
        const QString path = m_tmp.path() + "/launcher.json";
        QFile f(path); f.open(QIODevice::WriteOnly);
        f.write(R"({"mentionAction":"send_and_open","askCommand":["x","{query}"]})"); f.close();
        LauncherConfig c(path);
        QVERIFY(c.mentionOpensChat());
        QCOMPARE(c.askCommand(), QStringList({ "x", "{query}" }));
        QFile g(path); g.open(QIODevice::WriteOnly | QIODevice::Truncate); g.write("{broken"); g.close();
        c.load();
        QVERIFY(!c.mentionOpensChat());   // broken file = defaults
        QVERIFY(c.askCommand().isEmpty());
        QVERIFY(!LauncherConfig(m_tmp.path() + "/absent.json").mentionOpensChat());
    }
    // ---- launching
    void launchedProgramsDoNotInheritTheDaemonsQtEnvironment() {
        qputenv("QT_QPA_PLATFORM", "wayland");
        qputenv("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell");
        qputenv("TAOS_LAUNCHER_OWN_ENV", "QT_QPA_PLATFORM,QT_WAYLAND_SHELL_INTEGRATION");
        qputenv("SOMETHING_ELSE", "kept");
        const QProcessEnvironment env = childEnvironment();
        QVERIFY(!env.contains("QT_QPA_PLATFORM"));
        QVERIFY(!env.contains("QT_WAYLAND_SHELL_INTEGRATION"));
        QVERIFY(!env.contains("TAOS_LAUNCHER_OWN_ENV"));
        QCOMPARE(env.value("SOMETHING_ELSE"), QString("kept"));
        // and for real: the child writes its environment to a file
        const QString out = m_tmp.path() + "/childenv.txt";
        Action a; a.verb = "exec"; a.args = { "sh", "-c", "env > " + out };
        QString err;
        QVERIFY2(runAction(a, &err), qPrintable(err));
        QTRY_VERIFY_WITH_TIMEOUT(QFileInfo(out).size() > 0, 3000);
        QFile f(out); QVERIFY(f.open(QIODevice::ReadOnly));
        const QByteArray text = f.readAll();
        QVERIFY(!text.contains("QT_QPA_PLATFORM"));
        QVERIFY(!text.contains("QT_WAYLAND_SHELL_INTEGRATION"));
        QVERIFY(text.contains("SOMETHING_ELSE=kept"));
        // a daemon that set nothing of its own (the variables came from the session) strips nothing
        qputenv("TAOS_LAUNCHER_OWN_ENV", "");
        QVERIFY(childEnvironment().contains("QT_QPA_PLATFORM"));
        qunsetenv("QT_QPA_PLATFORM"); qunsetenv("QT_WAYLAND_SHELL_INTEGRATION"); qunsetenv("TAOS_LAUNCHER_OWN_ENV");
    }
    void focusedOutputFromSwayJson() {
        QCOMPARE(focusedOutputFromJson(R"([{"name":"DP-1","focused":false},{"name":"HDMI-A-1","focused":true}])"), QString("HDMI-A-1"));
        QCOMPARE(focusedOutputFromJson("[]"), QString());
        QCOMPARE(focusedOutputFromJson("garbage"), QString());
    }
    // ---- frecency
    void frecencyDecaysAndPersists() {
        const QString path = m_tmp.path() + "/fr.json";
        const qint64 t0 = 1'800'000'000;
        {
            Frecency f(path);
            f.record("app:a", t0); f.record("app:a", t0); f.record("app:b", t0);
            QVERIFY(f.boost("app:a", t0) > f.boost("app:b", t0));
            QCOMPARE(f.boost("app:never", t0), 0.0);
            const double fresh = f.boost("app:a", t0);
            const double later = f.boost("app:a", t0 + 60 * 24 * 3600);
            QVERIFY(later < fresh / 2);   // 60 days is four half lives
            QCOMPARE(f.top("app:", 5, t0).value(0), QString("app:a"));
        }
        Frecency again(path);
        QVERIFY(again.boost("app:a", QDateTime::currentSecsSinceEpoch()) >= 0);
        QFile g(path); g.open(QIODevice::WriteOnly | QIODevice::Truncate); g.write("{{{"); g.close();
        Frecency broken(path);   // corrupt file: empty, no crash, next save replaces it
        QCOMPARE(broken.boost("app:a"), 0.0);
        broken.record("app:c");
        QVERIFY(broken.boost("app:c") > 0);
    }
};
QTEST_MAIN(TestProviders)
#include "launcher_test_providers.moc"
