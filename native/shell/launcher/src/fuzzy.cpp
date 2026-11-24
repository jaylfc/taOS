// SPDX-License-Identifier: AGPL-3.0-or-later
#include "fuzzy.h"

#include <QStringList>

namespace taos::search {

static bool isBoundary(const QString &t, int i) {
    if (i == 0) return true;
    const QChar p = t[i - 1];
    if (!p.isLetterOrNumber()) return true;
    return p.isLower() && t[i].isUpper();
}

static double scoreWord(const QString &w, const QString &text) {
    if (w.isEmpty()) return 1.0;
    const QString lt = text.toLower();
    const QString lw = w.toLower();
    if (lt == lw) return 1.0;
    if (lt.startsWith(lw)) return 0.92 - 0.1 * (1.0 - double(lw.size()) / lt.size());
    // start of a later word
    for (int i = 1; i < lt.size(); ++i) {
        if (isBoundary(text, i) && QStringView(lt).mid(i).startsWith(lw)) return 0.84 - qMin(0.14, i * 0.01);
    }
    const int sub = lt.indexOf(lw);
    if (sub >= 0) return 0.62 - qMin(0.2, sub * 0.01);
    if (lw.size() < 2 || lt.size() > 200) return 0;
    // subsequence with bonuses for runs and word starts
    int ti = 0;
    double bonus = 0;
    int prev = -2;
    for (int qi = 0; qi < lw.size(); ++qi) {
        int found = -1;
        for (int j = ti; j < lt.size(); ++j) {
            if (lt[j] == lw[qi]) {
                // prefer a word start when one is close
                found = j;
                if (!isBoundary(text, j)) {
                    for (int k = j + 1; k < qMin(lt.size(), j + 4); ++k)
                        if (lt[k] == lw[qi] && isBoundary(text, k)) { found = k; break; }
                }
                break;
            }
        }
        if (found < 0) return 0;
        if (found == prev + 1) bonus += 1.0;
        else if (isBoundary(text, found)) bonus += 0.8;
        else bonus += 0.2;
        prev = found;
        ti = found + 1;
    }
    const double norm = bonus / lw.size();   // 0.2 .. 1.0
    const double lenPenalty = qMin(0.1, (lt.size() - lw.size()) * 0.002);
    return qMax(0.05, 0.15 + 0.35 * norm - lenPenalty);
}

double fuzzyScore(const QString &query, const QString &text) {
    const QStringList words = query.trimmed().split(QLatin1Char(' '), Qt::SkipEmptyParts);
    if (words.isEmpty() || text.isEmpty()) return 0;
    double sum = 0;
    for (const QString &w : words) {
        const double s = scoreWord(w, text);
        if (s <= 0) return 0;
        sum += s;
    }
    return sum / words.size();
}

}  // namespace taos::search
