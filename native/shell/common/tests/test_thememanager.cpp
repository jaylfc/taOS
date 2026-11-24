#include <QQmlComponent>
#include <QQmlContext>
#include <QQmlEngine>
#include <QtTest>

#include "thememanager.h"
#include "testenv.h"

static void writeAppearance(const QString &path, const QString &theme, const QString &mode) {
    Appearance a;
    a.theme = theme;
    a.mode = mode;
    QVERIFY(AppearanceFile::write(path, a));
}

class TestThemeManager : public QObject {
    Q_OBJECT
    static ThemeManager::Options baseOpts() {
        ThemeManager::Options o;
        o.transitionMs = 0;
        return o;
    }
private slots:
    void newPersonGetsGraphiteDark() {
        TestHome h;
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        QCOMPARE(m.mode(), QStringLiteral("dark"));
        QCOMPARE(m.variant(), QStringLiteral("dark"));
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#0B0B0C"));
    }
    void systemDefaultSeedsANewPerson() {
        TestHome h;
        writeAppearance(h.systemAppearance(), "graphite-warm", "light");
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite-warm"));
        QCOMPARE(m.variant(), QStringLiteral("light"));
    }
    void unknownSavedThemeFallsBackToGraphite() {
        TestHome h;
        writeAppearance(h.appearance(), "taos-default", "light");  // the removed indigo theme
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        QCOMPARE(m.variant(), QStringLiteral("light"));
        // The choice is not rewritten behind the person's back.
        Appearance a;
        AppearanceFile::read(h.appearance(), &a);
        QCOMPARE(a.theme, QStringLiteral("taos-default"));
    }
    void fileChangeReloadsLiveAndEmits() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite", "dark");
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QSignalSpy changed(&t, &Theme::changed);
        QSignalSpy appearance(&m, &ThemeManager::appearanceChanged);
        // What `taos-theme set` does: an atomic replace from another process.
        writeAppearance(h.appearance(), "graphite-ice", "light");
        QTRY_VERIFY_WITH_TIMEOUT(m.themeId() == "graphite-ice", 3000);
        QVERIFY(changed.count() >= 1);
        QVERIFY(appearance.count() >= 1);
        QCOMPARE(m.variant(), QStringLiteral("light"));
        QCOMPARE(t.accent().name().toUpper(), QStringLiteral("#1F5FD1"));
        // And again, to prove the watch survives the replaced inode.
        writeAppearance(h.appearance(), "graphite-warm", "dark");
        QTRY_VERIFY_WITH_TIMEOUT(m.themeId() == "graphite-warm", 3000);
        QCOMPARE(t.accent().name().toUpper(), QStringLiteral("#E8B26A"));
    }
    void windowButtonsFollowTheThemeAndThePersonLive() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite", "dark");
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(t.windowButtons(), QStringLiteral("left"));        // default
        QSignalSpy changed(&t, &Theme::changed);
        // The person's override arrives in the file, from another process (taos-theme set-buttons).
        Appearance a;
        a.theme = "graphite";
        a.mode = "dark";
        a.windowButtons = "right";
        QVERIFY(AppearanceFile::write(h.appearance(), a));
        QTRY_VERIFY_WITH_TIMEOUT(t.windowButtons() == "right", 3000);
        QVERIFY(changed.count() >= 1);
        QCOMPARE(m.windowButtonsChoice(), QStringLiteral("right"));
        // Picking a theme keeps the side.
        QVERIFY(m.setTheme("graphite-ice"));
        QCOMPARE(t.windowButtons(), QStringLiteral("right"));
        // The API: left, then back to the theme.
        QVERIFY(m.setWindowButtons("left"));
        QCOMPARE(t.windowButtons(), QStringLiteral("left"));
        Appearance saved;
        QVERIFY(AppearanceFile::read(h.appearance(), &saved));
        QCOMPARE(saved.windowButtons, QStringLiteral("left"));
        QCOMPARE(saved.theme, QStringLiteral("graphite-ice"));
        QVERIFY(m.setWindowButtons("theme"));
        QCOMPARE(m.windowButtonsChoice(), QStringLiteral("theme"));
        QVERIFY(AppearanceFile::read(h.appearance(), &saved));
        QVERIFY(saved.windowButtons.isEmpty());
        QVERIFY(!m.setWindowButtons("middle"));
    }
    void fileCreatedLaterIsPickedUp() {
        TestHome h;  // ~/.config/taos does not exist yet
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        writeAppearance(h.appearance(), "graphite-ice", "dark");
        QTRY_VERIFY_WITH_TIMEOUT(m.themeId() == "graphite-ice", 3000);
    }
    void boundQmlPropertyFollows() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite", "dark");
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QQmlEngine engine;
        engine.rootContext()->setContextProperty("Theme", &t);
        QQmlComponent c(&engine);
        c.setData("import QtQuick\nItem { property color bg: Theme.ground; property color fg: Theme.text; property color dim: Qt.rgba(Theme.accent.r, 0, 0, 1) }", QUrl());
        QScopedPointer<QObject> o(c.create());
        QVERIFY2(o, qPrintable(c.errorString()));
        QCOMPARE(o->property("bg").value<QColor>(), QColor("#0B0B0C"));
        writeAppearance(h.appearance(), "graphite", "light");
        QTRY_VERIFY_WITH_TIMEOUT(o->property("bg").value<QColor>() == QColor("#EBEBEC"), 3000);
        QCOMPARE(o->property("fg").value<QColor>(), QColor("#161617"));
    }
    void crossFadeEndsOnTheTarget() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite", "dark");
        Theme t;
        ThemeManager::Options o;
        o.transitionMs = 150;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QSignalSpy changed(&t, &Theme::changed);
        m.setMode("light");
        QVERIFY(t.transitioning());
        QTRY_VERIFY_WITH_TIMEOUT(!t.transitioning(), 1500);
        QVERIFY(changed.count() >= 3);
        QCOMPARE(t.ground().name().toUpper(), QStringLiteral("#EBEBEC"));
    }
    void reducedMotionSwitchesAtOnce() {
        TestHome h;
        Theme t;
        ThemeManager::Options o;
        o.transitionMs = 150;
        o.reduceMotion = true;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QSignalSpy changed(&t, &Theme::changed);
        m.setMode("light");
        QVERIFY(!t.transitioning());
        QCOMPARE(changed.count(), 1);
    }
    void autoModeWithAFakeClock() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite-ice", "auto");
        QDateTime fake(QDate(2026, 11, 22), QTime(23, 30));
        ThemeManager::Options o = baseOpts();
        o.clock = [&] { return fake; };
        Theme t;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QCOMPARE(m.mode(), QStringLiteral("auto"));
        QCOMPARE(m.variant(), QStringLiteral("dark"));
        QSignalSpy appearance(&m, &ThemeManager::appearanceChanged);
        fake = QDateTime(QDate(2026, 11, 23), QTime(7, 0));
        m.reload();  // what the minute timer does
        QCOMPARE(m.variant(), QStringLiteral("light"));
        QVERIFY(!t.dark());
        QCOMPARE(appearance.count(), 1);
        fake = QDateTime(QDate(2026, 11, 23), QTime(19, 1));
        m.reload();
        QCOMPARE(m.variant(), QStringLiteral("dark"));
        QVERIFY(m.announcementText().contains("auto"));
        QCOMPARE(m.announcementText(), QStringLiteral("Theme: Graphite + Ice, auto, dark now"));
    }
    void pickerModel() {
        TestHome h;
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QVariantList themes = m.themes();
        QCOMPARE(themes.size(), 3);
        QCOMPARE(themes[0].toMap()["name"].toString(), QStringLiteral("Graphite"));
        QCOMPARE(themes[1].toMap()["name"].toString(), QStringLiteral("Graphite + Ice"));
        QCOMPARE(themes[2].toMap()["name"].toString(), QStringLiteral("Warm Graphite"));
        QVERIFY(themes[0].toMap()["current"].toBool());
        QVERIFY(!themes[1].toMap()["current"].toBool());
        QCOMPARE(themes[1].toMap()["swatches"].toList().size(), 3);
        QCOMPARE(themes[1].toMap()["swatches"].toList()[2].value<QColor>().name().toUpper(), QStringLiteral("#7FB2FF"));  // dark accent
        const QVariantList modes = m.modes();
        QCOMPARE(modes.size(), 3);
        QCOMPARE(modes[0].toMap()["id"].toString(), QStringLiteral("dark"));
        QVERIFY(modes[0].toMap()["current"].toBool());

        QSignalSpy spoken(&m, &ThemeManager::announce);
        QSignalSpy listChanged(&m, &ThemeManager::themesChanged);
        QVERIFY(m.setTheme("graphite-ice"));
        QCOMPARE(spoken.count(), 1);
        QCOMPARE(spoken[0][0].toString(), QStringLiteral("Theme: Graphite + Ice, dark"));
        QVERIFY(listChanged.count() >= 1);
        themes = m.themes();
        QVERIFY(!themes[0].toMap()["current"].toBool());
        QVERIFY(themes[1].toMap()["current"].toBool());
        QVERIFY(m.setMode("light"));
        QCOMPARE(spoken.last()[0].toString(), QStringLiteral("Theme: Graphite + Ice, light"));
        // The chips show the swatches of the variant on screen.
        QCOMPARE(m.themes()[1].toMap()["swatches"].toList()[2].value<QColor>().name().toUpper(), QStringLiteral("#1F5FD1"));
        QVERIFY(m.modes()[1].toMap()["current"].toBool());
        // Saved: a second process (or the next login) sees it.
        Appearance a;
        QVERIFY(AppearanceFile::read(h.appearance(), &a));
        QCOMPARE(a.theme, QStringLiteral("graphite-ice"));
        QCOMPARE(a.mode, QStringLiteral("light"));
        // Rejected choices change nothing.
        QVERIFY(!m.setTheme("nope"));
        QVERIFY(!m.setMode("purple"));
        QCOMPARE(m.themeId(), QStringLiteral("graphite-ice"));
    }
    void ownWriteDoesNotLoopOrFlicker() {
        TestHome h;
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QVERIFY(m.setTheme("graphite-warm"));
        QSignalSpy changed(&t, &Theme::changed);
        QTest::qWait(300);  // the watcher sees our own atomic write
        QCOMPARE(changed.count(), 0);
    }
    void pinnedByCommandLineWinsOverTheFile() {
        TestHome h;
        writeAppearance(h.appearance(), "graphite-warm", "dark");
        Theme t;
        ThemeManager::Options o = baseOpts();
        o.pinnedVariant = "light";
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite-warm"));
        QCOMPARE(m.variant(), QStringLiteral("light"));
    }
    void newThirdPartyThemeAppearsInThePicker() {
        TestHome h;
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QCOMPARE(m.themes().size(), 3);
        QSignalSpy list(&m, &ThemeManager::themesChanged);
        writeTheme(h.userThemes(), "mine", goodTheme("mine"));
        QTRY_VERIFY_WITH_TIMEOUT(m.themes().size() == 4, 3000);
        QVERIFY(m.setTheme("mine"));
        QCOMPARE(t.accent().name().toUpper(), QStringLiteral("#7FB2FF"));
    }
    void sessionKeepsAWorldReadableCopyForTheGreeter() {
        TestHome h;
        QDir().mkpath(h.mirror());
        Theme t;
        ThemeManager::Options o = baseOpts();
        o.uid = 4242;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QVERIFY(m.setTheme("graphite-ice"));
        const QString p = h.mirror() + "/4242.json";
        QVERIFY(QFile::exists(p));
        QVERIFY(QFile::permissions(p) & QFileDevice::ReadOther);
        Appearance a;
        AppearanceFile::read(p, &a);
        QCOMPARE(a.theme, QStringLiteral("graphite-ice"));
    }
    void noMirrorFolderMeansNoCopy() {
        TestHome h;  // the mirror folder is not created
        Theme t;
        ThemeManager m(&t, baseOpts());
        QVERIFY(m.start());
        QVERIFY(m.setTheme("graphite-ice"));
        QVERIFY(!QFileInfo::exists(h.mirror()));
    }
    void greeterShowsTheSystemDefaultThenThePersonsChoice() {
        TestHome h;
        QDir().mkpath(h.mirror());
        writeAppearance(h.systemAppearance(), "graphite", "dark");
        writeAppearance(h.mirror() + "/1001.json", "graphite-warm", "light");
        // A person's own home file must never be read by the greeter.
        writeAppearance(h.appearance(), "graphite-ice", "light");
        Theme t;
        ThemeManager::Options o = baseOpts();
        o.role = ThemeManager::Role::Greeter;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        QCOMPARE(m.variant(), QStringLiteral("dark"));
        m.previewUser(1001);
        QCOMPARE(m.themeId(), QStringLiteral("graphite-warm"));
        QCOMPARE(m.variant(), QStringLiteral("light"));
        m.previewUser(1002);  // no copy: the system default
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        QCOMPARE(m.variant(), QStringLiteral("dark"));
        // The greeter never writes a choice.
        QVERIFY(m.setTheme("graphite-ice"));
        Appearance a;
        AppearanceFile::read(h.appearance(), &a);
        QCOMPARE(a.theme, QStringLiteral("graphite-ice"));  // untouched: still the person's own earlier value
        QVERIFY(!QFileInfo::exists(h.mirror() + "/1002.json"));
    }
    void greeterWithNothingConfiguredIsGraphiteDark() {
        TestHome h;
        Theme t;
        ThemeManager::Options o = baseOpts();
        o.role = ThemeManager::Role::Greeter;
        ThemeManager m(&t, o);
        QVERIFY(m.start());
        QCOMPARE(m.themeId(), QStringLiteral("graphite"));
        QCOMPARE(m.variant(), QStringLiteral("dark"));
    }
};

QTEST_MAIN(TestThemeManager)
#include "test_thememanager.moc"
