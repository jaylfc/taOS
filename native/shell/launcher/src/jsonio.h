// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QJsonArray>
#include <QJsonObject>

#include "engine.h"

namespace taos::search {
QJsonObject toJson(const Action &a);
QJsonObject toJson(const Result &r);
QJsonObject toJson(const Row &r);
QJsonArray toJson(const QList<Row> &rows);
}
