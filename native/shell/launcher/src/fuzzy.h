#pragma once
#include <QString>

namespace taos::search {

// Score `query` against `text`, 0 (no match) .. 1 (identical, ignoring case). Every
// space-separated word of the query must match; the score is their mean. Per word the best of:
// exact 1.0, prefix 0.92, start of a word 0.84, substring 0.62, subsequence 0.15..0.5.
double fuzzyScore(const QString &query, const QString &text);

}  // namespace taos::search
