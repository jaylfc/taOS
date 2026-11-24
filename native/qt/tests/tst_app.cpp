// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QFile>
#include <QGuiApplication>
#include <QSignalSpy>
#include <QtTest>

#include "taosqt/app.h"

using namespace Qt::StringLiterals;
using namespace Taos;

class TstApp : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void configureSetsTheWaylandAppIdAndNames()
    {
        App::configure({u"my.taos.testapp"_s, u"Test App"_s, u"1.2.3"_s});
        QCOMPARE(QGuiApplication::desktopFileName(), u"my.taos.testapp"_s);
        QCOMPARE(QGuiApplication::applicationDisplayName(), u"Test App"_s);
        QCOMPARE(QCoreApplication::applicationVersion(), u"1.2.3"_s);
    }

    void secondInstanceActivatesTheFirstAndIsToldToExit()
    {
        const QString id = u"my.taos.single-%1"_s.arg(QCoreApplication::applicationPid());
        SingleInstance first(id);
        QSignalSpy act(&first, &SingleInstance::activationRequested);
        QVERIFY(first.acquire());

        // The second launch runs in another process in real life; here a second
        // object is enough because it talks to the socket, not to the first object.
        SingleInstance second(id);
        QVERIFY(!second.acquire({u"--open"_s, u"note 7"_s}));
        QTRY_COMPARE(act.size(), 1);
        QCOMPARE(act.first().first().toStringList(), (QStringList{u"--open"_s, u"note 7"_s}));
    }

    void aStaleSocketDoesNotBlockTheNextStart()
    {
        const QString id = u"my.taos.stale-%1"_s.arg(QCoreApplication::applicationPid());
        {
            SingleInstance a(id);
            QVERIFY(a.acquire());
        } // closed: the next one starts normally
        SingleInstance b(id);
        QVERIFY(b.acquire());
    }

    void desktopTemplateCarriesTheAppIdAsStartupWMClass()
    {
        QFile f(QStringLiteral(TAOS_TEST_SRC_DIR "/../app/app.desktop.in"));
        QVERIFY(f.open(QIODevice::ReadOnly));
        const QString t = QString::fromUtf8(f.readAll());
        QVERIFY(t.contains(u"StartupWMClass=@APP_ID@"_s));
        QVERIFY(t.contains(u"Icon=@APP_ID@"_s));
        QVERIFY(t.contains(u"Name=@APP_DISPLAY_NAME@"_s));
        QVERIFY(t.contains(u"Exec=@APP_EXEC@"_s));
        QVERIFY(t.contains(u"SingleMainWindow=true"_s));
    }
};

QTEST_MAIN(TstApp)
#include "tst_app.moc"
