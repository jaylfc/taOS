#include "jsonio.h"

namespace taos::search {
QJsonObject toJson(const Action &a) {
    QJsonObject o{{QStringLiteral("id"), a.id}, {QStringLiteral("label"), a.label}, {QStringLiteral("verb"), a.verb}};
    if (!a.args.isEmpty()) o.insert(QStringLiteral("args"), QJsonArray::fromStringList(a.args));
    if (!a.data.isEmpty()) o.insert(QStringLiteral("data"), a.data);
    if (a.confirm) o.insert(QStringLiteral("confirm"), true);
    if (a.stub) o.insert(QStringLiteral("stub"), true);
    return o;
}
QJsonObject toJson(const Result &r) {
    QJsonArray acts;
    for (const Action &a : r.actions) acts << toJson(a);
    return QJsonObject{{QStringLiteral("id"), r.id}, {QStringLiteral("provider"), r.provider}, {QStringLiteral("kind"), r.kind},
                       {QStringLiteral("title"), r.title}, {QStringLiteral("subtitle"), r.subtitle}, {QStringLiteral("icon"), r.icon},
                       {QStringLiteral("score"), r.score}, {QStringLiteral("actions"), acts}};
}
QJsonObject toJson(const Row &r) {
    QJsonObject o = toJson(r.r);
    o.insert(QStringLiteral("section"), r.section);
    return o;
}
QJsonArray toJson(const QList<Row> &rows) {
    QJsonArray a;
    for (const Row &r : rows) a << toJson(r);
    return a;
}
}
