// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QDirIterator>
#include <QFile>
#include <QTemporaryDir>
#include <QtTest>

#include "shortcuts.h"

using namespace Shortcuts;

namespace {
void write(const QString &path, const QByteArray &text) {
    QDir().mkpath(QFileInfo(path).absolutePath());
    QFile f(path);
    QVERIFY(f.open(QIODevice::WriteOnly));
    f.write(text);
}
Registry realRegistry() {
    QFile f(QStringLiteral(TAOS_SHORTCUTS_JSON));
    const bool opened = f.open(QIODevice::ReadOnly);
    Registry r;
    QString err;
    const bool ok = opened && r.load(f.readAll(), &err);
    if (!ok) qFatal("shortcuts.json: %s", qPrintable(err.isEmpty() ? QStringLiteral("cannot open") : err));
    return r;
}
}  // namespace

class TestShortcuts : public QObject {
    Q_OBJECT
private slots:
    void parsesTheLoadedConfig() {
        QTemporaryDir d;
        write(d.filePath(QStringLiteral("config")), "# comment\nset $mod Mod4\nset $term foot\n"
              "bindsym $mod+Return exec $term\nbindsym --locked XF86AudioMute exec mute\n"
              "bindsym --release Super_L exec taos-overview overview\n"
              "bindsym $mod+Shift+q \\\n    kill\n"
              "mode \"resize\" {\n    bindsym Left resize shrink width 10px\n    bindsym Escape mode default\n}\n"
              "floating_modifier $mod normal\ninclude conf.d/*.conf\ninclude ~/definitely/not/here.conf\n");
        write(d.filePath(QStringLiteral("conf.d/10-a.conf")), "bindsym $mod+a exec a\nbindsym $mod+Return exec replaced\n");
        write(d.filePath(QStringLiteral("conf.d/20-b.conf")), "bindsym $mod+b exec b\n");
        const ParseResult p = parseConfig(d.filePath(QStringLiteral("config")));
        QStringList cmds;
        for (const Binding &b : p.bindings) cmds << b.combo + QLatin1Char('=') + b.command;
        QVERIFY2(cmds.contains(QStringLiteral("Mod4+Return=exec replaced")), qPrintable(cmds.join('|')));   // later wins
        QVERIFY(cmds.contains(QStringLiteral("Mod4+Shift+q=kill")));      // continuation line
        QVERIFY(cmds.contains(QStringLiteral("Mod4+a=exec a")) && cmds.contains(QStringLiteral("Mod4+b=exec b")));
        QVERIFY(!cmds.join('|').contains(QLatin1String("resize shrink")));   // another mode
        QCOMPARE(p.floatingModifier, QStringLiteral("Mod4"));
        QVERIFY(p.errors.size() == 1);                                      // the missing include is reported, not fatal
        bool rel = false, lock = false;
        for (const Binding &b : p.bindings) { if (b.combo == QLatin1String("Super_L")) rel = b.release; if (b.combo == QLatin1String("XF86AudioMute")) lock = b.locked; }
        QVERIFY(rel && lock);
    }
    void keysAreWrittenForPeople() {
        QCOMPARE(prettyKeys(QStringLiteral("Mod4+Shift+Return"), false), (QStringList { "Super", "Shift", "Enter" }));
        QCOMPARE(prettyKeys(QStringLiteral("Mod4+Mod1+Left"), false), (QStringList { "Super", "Alt", "Left" }));
        QCOMPARE(prettyKeys(QStringLiteral("Super_L"), true), (QStringList { "Tap Super" }));
        QCOMPARE(prettyKeys(QStringLiteral("Mod4+Control+Shift+h"), false), (QStringList { "Super", "Ctrl", "Shift", "H" }));
        QCOMPARE(prettyKey(QStringLiteral("XF86AudioRaiseVolume")), QStringLiteral("Volume up"));
    }
    void describedAndRawRows() {
        QTemporaryDir d;
        write(d.filePath(QStringLiteral("config")), "bindsym Mod4+q kill\nbindsym Mod4+1 workspace number 1\nbindsym Mod4+2 workspace number 2\n"
              "bindsym Mod4+F9 exec some-private-thing --flag\n");
        const QVector<Group> g = build(parseConfig(d.filePath(QStringLiteral("config"))), realRegistry());
        QStringList titles;
        for (const Group &x : g) titles << x.title;
        QVERIFY2(titles.contains(QStringLiteral("Windows")) && titles.contains(QStringLiteral("Desktops")), qPrintable(titles.join(',')));
        QCOMPARE(titles.last(), QStringLiteral("Other shortcuts"));
        const Group &other = g.last();
        QCOMPARE(other.rows[0].text, QStringLiteral("exec some-private-thing --flag"));   // raw form
        QVERIFY(!other.rows[0].described);
        for (const Group &x : g) if (x.title == QLatin1String("Desktops")) {
            int n = 0;
            for (const Row &r : x.rows) if (r.text.startsWith(QLatin1String("Go to desktop"))) { n++; QCOMPARE(r.keys.last(), QStringLiteral("1 to 9")); }
            QCOMPARE(n, 1);   // nine bindings, one row
        }
    }
    void gesturesForPointerAndForTouch() {
        const Registry r = realRegistry();
        const QVector<Group> pointer = build({}, r, false), touch = build({}, r, true);
        QStringList pt, tt;
        for (const Group &g : pointer) for (const Row &x : g.rows) pt << x.text;
        for (const Group &g : touch) for (const Row &x : g.rows) tt << x.text;
        QVERIFY(pt.join('|').contains(QLatin1String("Move a window")) && pt.join('|').contains(QLatin1String("Resize a window")));
        QVERIFY(!tt.isEmpty() && !tt.join('|').contains(QLatin1String("Resize a window")));
        QVERIFY(tt.join('|').contains(QLatin1String("close it")));
        QTemporaryDir d;
        write(d.filePath(QStringLiteral("config")), "bindsym Mod4+q kill\n");
        for (const Group &g : build(parseConfig(d.filePath(QStringLiteral("config"))), r, true)) for (const Row &x : g.rows) QVERIFY2(x.gesture, qPrintable(x.text));   // no key bindings on a phone
    }
    void searchAsYouType() {
        QTemporaryDir d;
        write(d.filePath(QStringLiteral("config")), "bindsym Mod4+q kill\nbindsym Mod4+f fullscreen toggle\nbindsym Mod4+space exec taos-launcher toggle\n");
        const QVector<Group> g = build(parseConfig(d.filePath(QStringLiteral("config"))), realRegistry());
        QCOMPARE(filter(g, QString()).size(), g.size());
        const QVector<Group> f = filter(g, QStringLiteral("FULL screen"));
        QCOMPARE(f.size(), 1);
        QCOMPARE(f[0].rows.size(), 1);
        QCOMPARE(filter(g, QStringLiteral("super space")).value(0).rows.value(0).text, QStringLiteral("App menu and search"));   // by keys
        QVERIFY(filter(g, QStringLiteral("zzzz")).isEmpty());
    }
    void appsDeclareTheirOwn() {
        QTemporaryDir d;
        write(d.filePath(QStringLiteral("my.taos.notes.json")), R"({"title":"Notes","shortcuts":[{"keys":["Ctrl","N"],"text":"New note"},{"keys":[],"text":"dropped"}]})");
        const Group g = appGroup(QStringLiteral("my.taos.notes"), QStringLiteral("Notes app"), { d.path() });
        QCOMPARE(g.title, QStringLiteral("Notes"));
        QCOMPARE(g.rows.size(), 1);
        QVERIFY(appGroup(QStringLiteral("../etc/passwd"), {}, { d.path() }).rows.isEmpty());
        QVERIFY(appGroup(QStringLiteral("nothing"), {}, { d.path() }).rows.isEmpty());
    }
    // The drift test: every binding the shell ships (its sway snippets and the base config the tests use) has a
    // description. A new binding without one fails here, in CI, until it is added to shortcuts.json.
    void everyShippedBindingIsDescribed() {
        const Registry r = realRegistry();
        QStringList files;
        QDirIterator it(QStringLiteral(TAOS_SHELL_DIR), { QStringLiteral("*.conf") }, QDir::Files, QDirIterator::Subdirectories);
        while (it.hasNext()) {
            const QString p = it.next();
            if (p.contains(QLatin1String("/sway/")) && !p.contains(QLatin1String("/tests/")) && !p.contains(QLatin1String("/build"))) files << p;
        }
        QDirIterator it2(QStringLiteral(TAOS_SHELL_DIR "/../compositor/sway"), { QStringLiteral("*.conf") }, QDir::Files);
        while (it2.hasNext()) files << it2.next();
        files << QStringLiteral(TAOS_SHELL_DIR "/window/tests/fixtures/base-reserved.conf");
        QVERIFY2(files.size() >= 8, qPrintable(QString::number(files.size())));
        QStringList missing;
        int total = 0;
        for (const QString &f : files) {
            for (const Binding &b : parseConfig(f).bindings) {
                if (b.command == QLatin1String("exec true")) continue;   // the test base config's placeholder
                total++;
                if (!r.find(b.command)) missing << QFileInfo(f).fileName() + QStringLiteral(": ") + b.combo + QLatin1Char(' ') + b.command;
            }
        }
        QVERIFY2(total > 30, qPrintable(QString::number(total)));
        QVERIFY2(missing.isEmpty(), qPrintable(QStringLiteral("bindings with no description in shortcuts.json:\n") + missing.join('\n')));
    }
};
QTEST_MAIN(TestShortcuts)
#include "test_shortcuts.moc"
