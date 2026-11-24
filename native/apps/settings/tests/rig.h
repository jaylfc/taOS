// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A complete Settings context on temporary folders and fake engines: nothing here touches the real
// ~/.config/taos, the compositor, the sound server or the system bus.
#include <QDir>
#include <QFile>
#include <QTemporaryDir>

#include "context.h"
#include "drmfixture.h"
#include "fakeaudio.h"
#include "fakesway.h"

struct Rig {
    QTemporaryDir dir;
    FakeSway sway;
    FakeAudioBackend audio;
    qint64 now = 1000000;
    TaosSettings::SettingsContext *ctx = nullptr;

    QString path(const QString &rel) const { return dir.path() + u'/' + rel; }

    static void isolateEnvironment(const QString &root)
    {
        qputenv("HOME", root.toUtf8());
        qputenv("XDG_CONFIG_HOME", (root + "/config").toUtf8());
        qputenv("XDG_DATA_HOME", (root + "/data").toUtf8());
        qputenv("XDG_RUNTIME_DIR", (root + "/run").toUtf8());
        qputenv("TAOS_APPEARANCE_MIRROR_DIR", (root + "/mirror").toUtf8());
        qputenv("TAOS_SYSTEM_APPEARANCE", (root + "/etc-appearance.json").toUtf8());
        qputenv("TAOS_SYSTEM_THEME_DIR", (root + "/system-themes").toUtf8());
        qunsetenv("SWAYSOCK");
        QDir().mkpath(root + "/run");
    }

    explicit Rig(const QString &fixture = QStringLiteral("owner.json"), bool withDbus = false)
    {
        isolateEnvironment(dir.path());
        if (!sway.load(QString(TAOS_DISPLAY_FIXTURES) + u'/' + fixture))
            qFatal("fixture %s", qPrintable(fixture));
        const QString drm = path("drm");
        QDir().mkpath(drm);
        if (fixture == u"owner.json")
            addConnector(drm, "HDMI-A-1", "3840x1200\n3840x1200\n3840x1200\n3840x2160\n", makeEdid(1043, 326));
        audio.st.connected = true;
        audio.st.defaultSink = "a";
        audio.st.defaultSource = "mic";
        audio.st.sinks << Fx::sink(1, "a", "Speakers") << Fx::sink(2, "b", "Headphones");
        audio.st.sources << Fx::source(10, "mic", "Built-in microphone");
        (void)withDbus;

        TaosSettings::Options o;
        o.theme.appearancePath = path("config/taos/appearance.json");
        o.theme.systemPath = path("etc-appearance.json");
        o.theme.mirrorDir = path("mirror");
        o.theme.themeDirs = {path("themes")};
        o.dockPath = path("config/taos/dock.json");
        o.desktopPath = path("config/taos/desktop.json");
        o.sway = &sway;
        o.displayConf = path("config/taos/sway.d/20-display.conf");
        o.drmRoot = drm;
        o.displayClock = [this] { return now; };
        o.watch = true;
        o.watchOutputs = false;
        o.audioBackend = &audio;
        o.dbusAddress = QStringLiteral("unix:path=/nonexistent/taos-test-bus");
        o.useDbus = false;
        o.osRoot = path("root");
        o.storagePath = dir.path();
        ctx = new TaosSettings::SettingsContext(o);
        ctx->display()->controller()->setSettleMs(0);
    }
    ~Rig() { delete ctx; }
};
