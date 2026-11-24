// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QJsonArray>
#include <QtTest>

#include "appearance.h"
#include "cli/themecli.h"
#include "testenv.h"

static int run(const QStringList &args, QString *out, QString *err) {
    QString o, e;
    QTextStream so(&o), se(&e);
    const int rc = runThemeCli(args, so, se);
    so.flush();
    se.flush();
    *out = o;
    *err = e;
    return rc;
}

class TestThemeCli : public QObject {
    Q_OBJECT
private slots:
    void listText() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"list"}, &o, &e), 0);
        QVERIFY(o.contains("graphite-ice"));
        QVERIFY(o.contains("Graphite + Ice"));
        QVERIFY(o.contains("Warm Graphite"));
        QVERIFY(!o.contains("taos-default"));
        QVERIFY(o.startsWith("* graphite "));  // the default is marked current
    }
    void listJson() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"list", "--json"}, &o, &e), 0);
        const QJsonObject r = QJsonDocument::fromJson(o.toUtf8()).object();
        QCOMPARE(r["themes"].toArray().size(), 3);
        const QJsonObject t1 = r["themes"].toArray()[1].toObject();
        QCOMPARE(t1["id"].toString(), QStringLiteral("graphite-ice"));
        QCOMPARE(t1["swatches"].toObject()["dark"].toArray().size(), 3);
        QCOMPARE(r["current"].toObject()["theme"].toString(), QStringLiteral("graphite"));
        QVERIFY(r["refused"].toArray().isEmpty());
    }
    void setButtons() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"get", "--json"}, &o, &e), 0);
        QJsonObject g = QJsonDocument::fromJson(o.toUtf8()).object();
        QCOMPARE(g["windowButtons"].toString(), QStringLiteral("left"));
        QCOMPARE(g["windowButtonsSetting"].toString(), QStringLiteral("theme"));
        QCOMPARE(run({"set-buttons", "right"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("Window buttons: right\n"));
        QCOMPARE(run({"get", "--json"}, &o, &e), 0);
        g = QJsonDocument::fromJson(o.toUtf8()).object();
        QCOMPARE(g["windowButtons"].toString(), QStringLiteral("right"));
        QCOMPARE(g["windowButtonsSetting"].toString(), QStringLiteral("right"));
        // A theme or mode choice keeps it.
        QCOMPARE(run({"set", "graphite-ice", "--mode", "light"}, &o, &e), 0);
        QCOMPARE(run({"get", "--json"}, &o, &e), 0);
        QCOMPARE(QJsonDocument::fromJson(o.toUtf8()).object()["windowButtons"].toString(), QStringLiteral("right"));
        QCOMPARE(run({"set-buttons", "theme"}, &o, &e), 0);
        QVERIFY(o.contains("from the theme"));
        QCOMPARE(run({"get", "--json"}, &o, &e), 0);
        QCOMPARE(QJsonDocument::fromJson(o.toUtf8()).object()["windowButtonsSetting"].toString(), QStringLiteral("theme"));
        QCOMPARE(run({"set-buttons", "middle"}, &o, &e), 2);
        QVERIFY(e.contains("left, right or theme"));
        QCOMPARE(run({"set-buttons"}, &o, &e), 2);
    }
    void exportForOtherApps() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"export", "button-layout"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("close,minimize,maximize:\n"));
        QCOMPARE(run({"set-buttons", "right"}, &o, &e), 0);
        QCOMPARE(run({"export", "button-layout"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral(":minimize,maximize,close\n"));
        QCOMPARE(run({"export", "foot"}, &o, &e), 0);
        QVERIFY2(o.contains("[csd]\npreferred=server\n"), "foot asks for the compositor's title bar (taos-frame draws the buttons)");
        QVERIFY(!o.contains("preferred=client"));
        QVERIFY(o.contains("\ncolor=ff131314\n"));          // graphite dark: the surface token
        QVERIFY(o.contains("\nbutton-close-color=ffe0786f\n"));   // the danger token
        QCOMPARE(run({"set", "graphite-ice", "--mode", "light"}, &o, &e), 0);
        QCOMPARE(run({"export", "foot"}, &o, &e), 0);
        QVERIFY(!o.contains("color=ff131314"));                // follows the theme and mode
        QCOMPARE(run({"export", "nothing"}, &o, &e), 2);
    }
    void getAndSet() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"get"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("graphite dark\n"));
        QCOMPARE(run({"set", "graphite-ice", "--mode", "auto"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("Theme: Graphite + Ice, auto\n"));
        QCOMPARE(run({"get"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("graphite-ice auto\n"));
        QCOMPARE(run({"get", "--json"}, &o, &e), 0);
        QCOMPARE(QJsonDocument::fromJson(o.toUtf8()).object()["theme"].toString(), QStringLiteral("graphite-ice"));
        // Theme only: the mode stays. Mode only: the theme stays.
        QCOMPARE(run({"set", "graphite-warm"}, &o, &e), 0);
        QCOMPARE(run({"get"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("graphite-warm auto\n"));
        QCOMPARE(run({"set", "--mode", "light"}, &o, &e), 0);
        QCOMPARE(run({"get"}, &o, &e), 0);
        QCOMPARE(o, QStringLiteral("graphite-warm light\n"));
        Appearance a;
        QVERIFY(AppearanceFile::read(h.appearance(), &a));
        QCOMPARE(a.theme, QStringLiteral("graphite-warm"));
    }
    void setUnknownThemeFailsAndChangesNothing() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({"set", "taos-default"}, &o, &e), 1);
        QVERIFY(e.contains("no theme called 'taos-default'"));
        QVERIFY(e.contains("graphite-warm"));
        QVERIFY(!QFileInfo::exists(h.appearance()));
    }
    void badUsage() {
        TestHome h;
        QString o, e;
        QCOMPARE(run({}, &o, &e), 2);
        QCOMPARE(run({"set"}, &o, &e), 2);
        QCOMPARE(run({"set", "graphite", "--mode", "purple"}, &o, &e), 2);
        QCOMPARE(run({"frobnicate"}, &o, &e), 2);
        QCOMPARE(run({"list", "--bogus"}, &o, &e), 2);
    }
    void refusedThemeIsReportedWithTheReason() {
        TestHome h;
        QJsonObject o = goodTheme("shy");
        QJsonObject colors = o["colors"].toObject();
        QJsonObject dark = colors["dark"].toObject();
        dark["text"] = "#202024";
        colors["dark"] = dark;
        o["colors"] = colors;
        writeTheme(h.userThemes(), "shy", o);
        QString out, err;
        QCOMPARE(run({"list"}, &out, &err), 0);
        QVERIFY(!out.contains("shy"));
        QVERIFY(err.contains("refused shy"));
        QVERIFY(err.contains("4.5:1"));
        QCOMPARE(run({"set", "shy"}, &out, &err), 1);
        QVERIFY(err.contains("was refused"));
        QCOMPARE(run({"list", "--json"}, &out, &err), 0);
        QCOMPARE(QJsonDocument::fromJson(out.toUtf8()).object()["refused"].toArray().size(), 1);
    }
    void theRealBinary() {
        TestHome h;
        QProcess p;
        p.start(TAOS_THEME_BIN, {"set", "graphite-ice", "--mode", "light"});
        QVERIFY(p.waitForFinished(10000));
        QCOMPARE(p.exitCode(), 0);
        p.start(TAOS_THEME_BIN, {"get"});
        QVERIFY(p.waitForFinished(10000));
        QCOMPARE(QString::fromUtf8(p.readAllStandardOutput()), QStringLiteral("graphite-ice light\n"));
    }
};

QTEST_MAIN(TestThemeCli)
#include "test_themecli.moc"
