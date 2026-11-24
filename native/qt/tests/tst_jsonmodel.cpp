// SPDX-License-Identifier: AGPL-3.0-or-later
#include <QAbstractItemModelTester>
#include <QSignalSpy>
#include <QtTest>

#include "taosqt/jsonmodel.h"

using namespace Qt::StringLiterals;
using namespace Taos;

static QJsonArray rows(std::initializer_list<std::pair<int, const char *>> l)
{
    QJsonArray a;
    for (const auto &p : l)
        a.append(QJsonObject{{u"id"_s, p.first}, {u"title"_s, QString::fromUtf8(p.second)}});
    return a;
}

class TstJsonModel : public QObject
{
    Q_OBJECT
private Q_SLOTS:
    void rolesAndData()
    {
        JsonListModel m({u"id"_s, u"title"_s});
        QAbstractItemModelTester t(&m);
        m.setRows(rows({{1, "a"}, {2, "b"}}));
        QCOMPARE(m.rowCount(), 2);
        const auto names = m.roleNames();
        QVERIFY(names.values().contains("title"));
        QVERIFY(names.values().contains("item"));
        const int titleRole = names.key("title");
        QCOMPARE(m.data(m.index(1), titleRole).toString(), u"b"_s);
        QCOMPARE(m.get(0).value(u"title"_s).toString(), u"a"_s);
    }

    void updatesInPlaceInsertsRemovesAndMoves()
    {
        JsonListModel m({u"id"_s, u"title"_s});
        QAbstractItemModelTester t(&m);
        m.setRows(rows({{1, "a"}, {2, "b"}, {3, "c"}}));
        QSignalSpy reset(&m, &QAbstractItemModel::modelReset);
        QSignalSpy ins(&m, &QAbstractItemModel::rowsInserted);
        QSignalSpy rem(&m, &QAbstractItemModel::rowsRemoved);
        QSignalSpy mov(&m, &QAbstractItemModel::rowsMoved);
        QSignalSpy chg(&m, &QAbstractItemModel::dataChanged);

        m.setRows(rows({{1, "a"}, {2, "B"}, {3, "c"}})); // one field changed
        QCOMPARE(chg.size(), 1);
        QCOMPARE(chg.first().first().toModelIndex().row(), 1);
        QCOMPARE(ins.size() + rem.size() + mov.size(), 0);

        m.setRows(rows({{4, "new"}, {1, "a"}, {2, "B"}, {3, "c"}})); // new row on top
        QCOMPARE(ins.size(), 1);
        QCOMPARE(m.get(0).value(u"id"_s).toInt(), 4);

        m.setRows(rows({{4, "new"}, {2, "B"}, {3, "c"}})); // one gone
        QCOMPARE(rem.size(), 1);
        QCOMPARE(m.rowCount(), 3);

        m.setRows(rows({{3, "c"}, {4, "new"}, {2, "B"}})); // reordered
        QVERIFY(mov.size() >= 1);
        QCOMPARE(m.get(0).value(u"id"_s).toInt(), 3);
        QCOMPARE(m.get(2).value(u"id"_s).toInt(), 2);
        QCOMPARE(reset.size(), 0); // never a reset: views keep scroll and selection

        m.setRows({});
        QCOMPARE(m.rowCount(), 0);
    }

    void duplicateKeysAndNonObjectsAreIgnored()
    {
        JsonListModel m({u"id"_s});
        QAbstractItemModelTester t(&m);
        QJsonArray a = rows({{1, "a"}, {1, "dup"}});
        a.append(5);
        m.setRows(a);
        QCOMPARE(m.rowCount(), 1);
    }
};

QTEST_MAIN(TstJsonModel)
#include "tst_jsonmodel.moc"
