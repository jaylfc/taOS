#include <QJsonDocument>
#include <QJsonObject>
#include <QSignalSpy>
#include <QtTest>

#include "instance.h"
#include "rig.h"

using namespace TaosSettings;

static QJsonObject readJson(const QString &p)
{
    QFile f(p);
    return f.open(QIODevice::ReadOnly) ? QJsonDocument::fromJson(f.readAll()).object() : QJsonObject();
}
static void writeText(const QString &p, const QByteArray &t)
{
    QDir().mkpath(QFileInfo(p).absolutePath());
    QFile f(p);
    QVERIFY(f.open(QIODevice::WriteOnly | QIODevice::Truncate));
    f.write(t);
}

class TestSettings : public QObject {
    Q_OBJECT
private slots:
    // ---- catalog and search -----------------------------------------------------------------
    void catalogIsConsistent() {
        std::unique_ptr<Catalog> c(Catalog::withBuiltins());
        QSet<QString> ids;
        for (const SettingEntry &s : c->settingList()) {
            QVERIFY2(!ids.contains(s.id), qPrintable(s.id));
            ids << s.id;
            QVERIFY2(c->hasSection(s.section), qPrintable(s.id));
            QVERIFY(!s.title.isEmpty());
        }
        QSet<QString> secs;
        for (const SectionEntry &s : c->sectionList()) { QVERIFY(!secs.contains(s.id)); secs << s.id; QVERIFY(!s.component.isEmpty()); }
        QVERIFY(c->sectionList().size() >= 7);
    }
    void searchFindsSettingsByTitleAndKeyword() {
        std::unique_ptr<Catalog> c(Catalog::withBuiltins());
        auto ids = [&](const QString &q) { QStringList l; for (const QVariant &v : c->search(q)) l << v.toMap()["id"].toString(); return l; };
        QVERIFY(c->search(QString()).isEmpty());
        QVERIFY(c->search(QStringLiteral("   ")).isEmpty());
        QCOMPARE(ids(QStringLiteral("dark")).first(), QStringLiteral("appearance.mode"));           // keyword
        QVERIFY(ids(QStringLiteral("wifi")).contains(QStringLiteral("network.wifi")));
        QVERIFY(ids(QStringLiteral("magnification")).contains(QStringLiteral("dock.magnify")));
        QVERIFY(ids(QStringLiteral("zoom")).contains(QStringLiteral("display.scale")));             // keyword of another setting
        QVERIFY(ids(QStringLiteral("zoom")).contains(QStringLiteral("dock.magnify")));
        QVERIFY(ids(QStringLiteral("mic mute")).contains(QStringLiteral("sound.inputMute")));        // two words, both needed
        QVERIFY(!ids(QStringLiteral("mic mute")).contains(QStringLiteral("dock.size")));
        QVERIFY(ids(QStringLiteral("qqqzzz")).isEmpty());
        // a title match beats a keyword match
        const auto vol = c->search(QStringLiteral("volume"));
        QVERIFY(!vol.isEmpty());
        QCOMPARE(vol.first().toMap()["id"].toString(), QStringLiteral("sound.volume"));
        // sections are results too
        QCOMPARE(c->search(QStringLiteral("bluetooth")).first().toMap()["section"].toString(), QStringLiteral("network"));
    }
    void pluginSectionsSlotIn() {
        std::unique_ptr<Catalog> c(Catalog::withBuiltins());
        c->addSection({QStringLiteral("plug"), QStringLiteral("Printers"), QStringLiteral("about"), QStringLiteral("Plug.qml"), {QStringLiteral("print")}});
        c->addSetting({QStringLiteral("plug.default"), QStringLiteral("plug"), QStringLiteral("Default printer"), QString(), {}});
        QCOMPARE(c->search(QStringLiteral("print")).first().toMap()["section"].toString(), QStringLiteral("plug"));
        QCOMPARE(c->component(QStringLiteral("plug")), QStringLiteral("Plug.qml"));
    }

    // ---- navigation and deep links ------------------------------------------------------------
    void deepLinks() {
        std::unique_ptr<Catalog> c(Catalog::withBuiltins());
        Navigator n(c.get());
        QCOMPARE(n.section(), QStringLiteral("appearance"));
        QSignalSpy reveal(&n, &Navigator::revealRequested);
        QVERIFY(n.applyArguments({QStringLiteral("--section"), QStringLiteral("dock"), QStringLiteral("--setting"), QStringLiteral("dock.autohide")}));
        QCOMPARE(n.section(), QStringLiteral("dock"));
        QCOMPARE(n.setting(), QStringLiteral("dock.autohide"));
        QCOMPARE(reveal.size(), 1);
        QVERIFY(n.applyArguments({QStringLiteral("--section=sound")}));
        QCOMPARE(n.section(), QStringLiteral("sound"));
        QVERIFY(n.setting().isEmpty());
        QVERIFY(n.applyArguments({QStringLiteral("--setting=network.wifi")}));    // a setting alone implies its section
        QCOMPARE(n.section(), QStringLiteral("network"));
        QVERIFY(!n.applyArguments({QStringLiteral("--section"), QStringLiteral("nope")}));
        QCOMPARE(n.section(), QStringLiteral("network"));                           // unchanged
        QVERIFY(!n.navigate(QStringLiteral("dock"), QStringLiteral("sound.volume"))); // setting of another section
        QVERIFY(n.applyArguments({QStringLiteral("--search"), QStringLiteral("dark")}));
        QCOMPARE(n.query(), QStringLiteral("dark"));
        QVERIFY(n.navigate(QStringLiteral("about")));
        QVERIFY(n.query().isEmpty());                                               // choosing a page leaves the search
    }
    void singleInstanceForwardsArguments() {
        QTemporaryDir run;
        qputenv("XDG_RUNTIME_DIR", run.path().toUtf8());
        InstanceGuard first;
        QVERIFY(first.socketPath().startsWith(run.path() + "/taos/"));
        QCOMPARE(QFileInfo(run.path() + "/taos").permissions() & (QFile::ReadGroup | QFile::ReadOther | QFile::ExeOther), QFileDevice::Permissions());
        QVERIFY(first.acquire({}));
        QSignalSpy got(&first, &InstanceGuard::activated);
        bool second = true;
        QTimer::singleShot(0, this, [&] { InstanceGuard other; second = other.acquire({QStringLiteral("--section"), QStringLiteral("display")}); });
        QTRY_COMPARE_WITH_TIMEOUT(got.size(), 1, 5000);
        QVERIFY(!second);
        QCOMPARE(got.first().first().toStringList(), (QStringList{QStringLiteral("--section"), QStringLiteral("display")}));
    }

    // ---- appearance (the theme engine) ---------------------------------------------------------
    void appearanceRoundTripAndExternalChange() {
        Rig r;
        ThemeManager *t = r.ctx->themes();
        const QString file = r.path("config/taos/appearance.json");
        QVERIFY(t->themes().size() >= 3);
        QVERIFY(t->setMode(QStringLiteral("light")));
        QCOMPARE(readJson(file)["mode"].toString(), QStringLiteral("light"));
        QCOMPARE(t->variant(), QStringLiteral("light"));
        QVERIFY(t->setTheme(QStringLiteral("graphite-warm")));
        QCOMPARE(readJson(file)["theme"].toString(), QStringLiteral("graphite-warm"));
        QVERIFY(!t->setTheme(QStringLiteral("no-such-theme")));
        // what `taos-theme set` does from a terminal shows up live
        QSignalSpy sp(t, &ThemeManager::appearanceChanged);
        writeText(file, "{\"theme\":\"graphite-ice\",\"mode\":\"dark\"}");
        QTRY_COMPARE_WITH_TIMEOUT(t->themeId(), QStringLiteral("graphite-ice"), 5000);
        QCOMPARE(t->mode(), QStringLiteral("dark"));
        QVERIFY(!sp.isEmpty());
    }

    // ---- dock ----------------------------------------------------------------------------------
    void dockRoundTripKeepsOtherKeys() {
        Rig r;
        const QString file = r.path("config/taos/dock.json");
        writeText(file, "{\"position\":\"left\",\"pinned\":[\"a.desktop\",\"b.desktop\"],\"outputs\":\"all\"}");
        DockSettings *d = r.ctx->dock();
        d->file()->reload();
        QCOMPARE(d->position(), QStringLiteral("left"));
        QVERIFY(d->set(QStringLiteral("autohide"), true));
        QVERIFY(d->set(QStringLiteral("size"), QStringLiteral("large")));
        QVERIFY(d->set(QStringLiteral("hideInTiling"), false));
        const QJsonObject o = readJson(file);
        QCOMPARE(o["autohide"].toBool(), true);
        QCOMPARE(o["size"].toString(), QStringLiteral("large"));
        QCOMPARE(o["hideInTiling"].toBool(), false);
        QCOMPARE(o["pinned"].toArray().size(), 2);                      // not ours: survives
        QCOMPARE(o["outputs"].toString(), QStringLiteral("all"));
        QVERIFY(!d->set(QStringLiteral("position"), QStringLiteral("top")));
        QVERIFY(!d->set(QStringLiteral("size"), QStringLiteral("huge")));
        QVERIFY(!d->set(QStringLiteral("nope"), 1));
        QCOMPARE(d->position(), QStringLiteral("left"));
    }
    void dockDefaultsAndExternalChange() {
        Rig r;
        DockSettings *d = r.ctx->dock();
        QCOMPARE(d->position(), QStringLiteral("bottom"));
        QCOMPARE(d->size(), QStringLiteral("medium"));
        QVERIFY(d->hideInTiling());
        QVERIFY(!d->magnify());
        QSignalSpy sp(d, &DockSettings::changed);
        writeText(r.path("config/taos/dock.json"), "{\"position\":\"right\",\"magnify\":true}");   // `taos-dock set` from a terminal
        QTRY_COMPARE_WITH_TIMEOUT(d->position(), QStringLiteral("right"), 5000);
        QVERIFY(d->magnify());
        QVERIFY(!sp.isEmpty());
        writeText(r.path("config/taos/dock.json"), "not json");
        QTRY_VERIFY_WITH_TIMEOUT(!d->errorText().isEmpty(), 5000);
        QCOMPARE(d->position(), QStringLiteral("bottom"));   // defaults while the file is unreadable
    }

    // ---- desktop and windows -------------------------------------------------------------------
    void desktopModes() {
        Rig r;
        DesktopSettings *d = r.ctx->desktop();
        QCOMPARE(d->defaultMode(), QStringLiteral("desktop"));
        QVERIFY(d->setDefaultMode(QStringLiteral("tiling")));
        QCOMPARE(readJson(r.path("config/taos/desktop.json"))["default"].toString(), QStringLiteral("tiling"));
        QVERIFY(!d->setDefaultMode(QStringLiteral("floaty")));
        writeText(r.path("config/taos/desktop.json"), "{\"default\":\"desktop\",\"workspaces\":{\"2\":\"tiling\",\"1\":\"desktop\"}}");
        QTRY_COMPARE_WITH_TIMEOUT(d->workspaces().size(), 2, 5000);
        QCOMPARE(d->workspaces().first().toMap()["name"].toString(), QStringLiteral("1"));
        QVERIFY(d->useDefaultFor(QStringLiteral("2")));
        QCOMPARE(d->workspaces().size(), 1);
        QCOMPARE(readJson(r.path("config/taos/desktop.json"))["workspaces"].toObject().size(), 1);
        QVERIFY(!r.ctx->shortcuts().isEmpty());
    }

    // ---- display: the engine's trial, with a fake compositor and a fake clock -----------------
    void displayRevertsAfterTenSeconds() {
        Rig r;
        DisplayPage *p = r.ctx->display();
        QCOMPARE(p->state(), QStringLiteral("ready"));
        DisplayController *c = p->controller();
        QVariantMap out = c->outputs().first().toMap();
        const QString name = out["name"].toString();
        const QString before = out["modeText"].toString();
        int target = 0;
        for (const QVariant &v : out["rates"].toList())
            if (!v.toMap()["current"].toBool()) { target = v.toMap()["mHz"].toInt(); break; }
        QVERIFY2(target > 0, "the fixture offers another refresh rate");
        QVERIFY(c->setRefresh(name, target));
        QVERIFY(c->trialActive());
        QCOMPARE(c->trialSecondsLeft(), DisplayController::kConfirmSeconds);
        r.now += 4000;
        c->poll();
        QVERIFY(c->trialActive());
        QCOMPARE(c->trialSecondsLeft(), 6);
        r.now += 7000;
        c->poll();                                        // nobody pressed Keep
        QVERIFY(!c->trialActive());
        QCOMPARE(c->outputs().first().toMap()["modeText"].toString(), before);
    }
    void displayKeepSavesTheMode() {
        Rig r;
        DisplayController *c = r.ctx->display()->controller();
        const QVariantMap out = c->outputs().first().toMap();
        int target = 0;
        for (const QVariant &v : out["rates"].toList())
            if (!v.toMap()["current"].toBool()) { target = v.toMap()["mHz"].toInt(); break; }
        QVERIFY(c->setRefresh(out["name"].toString(), target));
        c->confirm();
        QVERIFY(!c->trialActive());
        QVERIFY(!c->outputs().first().toMap()["modeText"].toString().isEmpty());
        QVERIFY(QFileInfo::exists(r.path("config/taos/sway.d/20-display.conf")));
        // scale applies at once and is saved
        QVERIFY(c->setScale(out["name"].toString(), 1.5));
        QCOMPARE(c->outputs().first().toMap()["scalePercent"].toInt(), 150);
    }
    void displayWithoutACompositorSaysSo() {
        Rig r;
        r.sway.failReads = true;
        r.ctx->display()->reload();
        QCOMPARE(r.ctx->display()->state(), QStringLiteral("error"));
        QVERIFY(!r.ctx->display()->message().isEmpty());
        r.sway.failReads = false;
        r.ctx->display()->reload();
        QCOMPARE(r.ctx->display()->state(), QStringLiteral("ready"));
    }

    // ---- sound (the bar's audio model on a fake sound server) ---------------------------------
    void soundControlsReachTheBackend() {
        Rig r;
        Audio *a = r.ctx->audio();
        QVERIFY(a->available());
        QVERIFY(a->hasOutput());
        QVERIFY(a->hasInput());
        const int active = r.ctx->activeRow(a->outputs());
        QVERIFY(active >= 0);
        QCOMPARE(a->outputs()->rows().at(active).title, QStringLiteral("Speakers"));
        a->setPercent(35);
        QCOMPARE(r.audio.st.sinks[0].volume, 35);
        a->setMuted(true);
        QVERIFY(r.audio.st.sinks[0].mute);
        a->setInputMuted(true);
        QVERIFY(r.audio.st.sources[0].mute);
        a->selectOutput(a->outputs()->rows().at(1 - active).id);
        QCOMPARE(r.audio.st.defaultSink, QStringLiteral("b"));
        QCOMPARE(a->outputs()->rows().at(r.ctx->activeRow(a->outputs())).title, QStringLiteral("Headphones"));
    }

    // ---- about -------------------------------------------------------------------------------
    void aboutReadsTheSystem() {
        Rig r;
        writeText(r.path("root/etc/hostname"), "orange-desk\n");
        writeText(r.path("root/etc/os-release"), "NAME=\"Debian GNU/Linux\"\nPRETTY_NAME=\"Debian GNU/Linux 13 (trixie)\"\n");
        writeText(r.path("root/proc/cpuinfo"), "processor\t: 0\nmodel name\t: Fake Core 9\nprocessor\t: 1\nmodel name\t: Fake Core 9\n");
        writeText(r.path("root/proc/meminfo"), "MemTotal:       16384000 kB\nMemFree: 1 kB\n");
        r.ctx->about()->refresh();
        QCOMPARE(r.ctx->about()->deviceName(), QStringLiteral("orange-desk"));
        QCOMPARE(r.ctx->about()->osName(), QStringLiteral("Debian GNU/Linux 13 (trixie)"));
        QString joined;
        for (const QVariant &v : r.ctx->about()->rows()) joined += v.toMap()["label"].toString() + "=" + v.toMap()["value"].toString() + ";";
        QVERIFY2(joined.contains("Fake Core 9, 2 cores"), qPrintable(joined));
        QVERIFY2(joined.contains("17 GB") || joined.contains("16 GB"), qPrintable(joined));
        QVERIFY2(joined.contains("Storage="), qPrintable(joined));
    }
};

QTEST_MAIN(TestSettings)
#include "tst_settings.moc"
