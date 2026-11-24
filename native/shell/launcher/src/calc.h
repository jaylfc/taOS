#pragma once
#include <QString>

namespace taos::search {

struct CalcResult {
    bool ok = false;
    double value = 0;
    QString text;    // the value formatted for display and for the clipboard
    QString error;   // set when !ok
    bool isExpression = false;   // had an operator, a function or parentheses (not a bare number or word)
};

// A safe expression evaluator: a recursive descent parser, no eval of any kind. Numbers, + - * / % ^ **,
// x and the multiplication sign as times, parentheses, unary minus, constants pi and e, and the
// functions sqrt cbrt abs sin cos tan asin acos atan ln log log2 exp floor ceil round.
// Input longer than 256 characters, nesting deeper than 48 and non finite results are errors.
CalcResult evaluate(const QString &input);
QString formatNumber(double v);

}  // namespace taos::search
