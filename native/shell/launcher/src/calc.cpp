// SPDX-License-Identifier: AGPL-3.0-or-later
#include "calc.h"

#include <QLocale>
#include <cmath>

namespace taos::search {

namespace {

struct Parser {
    QString s;
    int pos = 0;
    int depth = 0;
    QString err;
    bool sawOp = false;

    void skip() { while (pos < s.size() && s[pos].isSpace()) ++pos; }
    bool eat(QChar c) { skip(); if (pos < s.size() && s[pos] == c) { ++pos; return true; } return false; }
    bool fail(const QString &m) { if (err.isEmpty()) err = m; return false; }

    bool expr(double *out) {
        if (++depth > 48) { --depth; return fail(QStringLiteral("too deeply nested")); }
        const bool ok = sum(out);
        --depth;
        return ok;
    }
    bool sum(double *out) {
        if (!term(out)) return false;
        for (;;) {
            skip();
            if (pos >= s.size()) return true;
            const QChar c = s[pos];
            if (c != QLatin1Char('+') && c != QLatin1Char('-') && c != QChar(0x2212)) return true;
            ++pos; sawOp = true;
            double r;
            if (!term(&r)) return false;
            *out = (c == QLatin1Char('+')) ? *out + r : *out - r;
        }
    }
    bool term(double *out) {
        if (!unary(out)) return false;
        for (;;) {
            skip();
            if (pos >= s.size()) return true;
            const QChar c = s[pos];
            const bool star = c == QLatin1Char('*') || c == QChar(0x00D7) || c == QLatin1Char('x') || c == QLatin1Char('X');
            if (c == QLatin1Char('*') && pos + 1 < s.size() && s[pos + 1] == QLatin1Char('*')) return true;   // ** is power, handled below
            const bool div = c == QLatin1Char('/') || c == QChar(0x00F7);
            const bool mod = c == QLatin1Char('%');
            if (!star && !div && !mod) return true;
            ++pos; sawOp = true;
            double r;
            if (!unary(&r)) return false;
            if (star) *out *= r;
            else if (div) { if (r == 0) return fail(QStringLiteral("division by zero")); *out /= r; }
            else { if (r == 0) return fail(QStringLiteral("division by zero")); *out = std::fmod(*out, r); }
        }
    }
    bool unary(double *out) {
        skip();
        if (pos < s.size() && (s[pos] == QLatin1Char('-') || s[pos] == QChar(0x2212))) {
            ++pos; sawOp = true;
            if (++depth > 48) { --depth; return fail(QStringLiteral("too deeply nested")); }
            const bool ok = unary(out);
            --depth;
            if (ok) *out = -*out;
            return ok;
        }
        if (pos < s.size() && s[pos] == QLatin1Char('+')) { ++pos; return unary(out); }
        return power(out);
    }
    bool power(double *out) {
        if (!atom(out)) return false;
        skip();
        bool pw = false;
        if (pos < s.size() && s[pos] == QLatin1Char('^')) { ++pos; pw = true; }
        else if (pos + 1 < s.size() && s[pos] == QLatin1Char('*') && s[pos + 1] == QLatin1Char('*')) { pos += 2; pw = true; }
        if (!pw) return true;
        sawOp = true;
        double r;
        if (++depth > 48) { --depth; return fail(QStringLiteral("too deeply nested")); }
        const bool ok = unary(&r);   // right associative, and allows 2^-1
        --depth;
        if (!ok) return false;
        *out = std::pow(*out, r);
        return true;
    }
    bool atom(double *out) {
        skip();
        if (pos >= s.size()) return fail(QStringLiteral("unexpected end"));
        const QChar c = s[pos];
        if (c == QLatin1Char('(')) {
            ++pos; sawOp = true;
            if (!expr(out)) return false;
            if (!eat(QLatin1Char(')'))) return fail(QStringLiteral("missing )"));
            return true;
        }
        if (c.isDigit() || c == QLatin1Char('.')) return number(out);
        if (c.isLetter()) {
            int st = pos;
            while (pos < s.size() && (s[pos].isLetter() || s[pos].isDigit())) ++pos;
            const QString name = s.mid(st, pos - st).toLower();
            if (name == QLatin1String("pi")) { *out = M_PI; return true; }
            if (name == QLatin1String("e")) { *out = M_E; return true; }
            if (!eat(QLatin1Char('('))) return fail(QStringLiteral("unknown name %1").arg(name));
            sawOp = true;
            double a;
            if (!expr(&a)) return false;
            if (!eat(QLatin1Char(')'))) return fail(QStringLiteral("missing )"));
            return apply(name, a, out);
        }
        return fail(QStringLiteral("unexpected %1").arg(c));
    }
    bool number(double *out) {
        const int st = pos;
        bool dot = false, any = false;
        while (pos < s.size()) {
            const QChar c = s[pos];
            if (c.isDigit()) { any = true; ++pos; }
            else if (c == QLatin1Char('.') && !dot) { dot = true; ++pos; }
            else if ((c == QLatin1Char(',') || c == QLatin1Char('_')) && any && pos + 1 < s.size() && s[pos + 1].isDigit()) ++pos;   // 1,000 and 1_000
            else break;
        }
        if (!any) return fail(QStringLiteral("bad number"));
        // exponent: 1e5, 2.5e-3 (only when digits follow, so "2e" stays the constant e)
        if (pos < s.size() && (s[pos] == QLatin1Char('e') || s[pos] == QLatin1Char('E'))) {
            int p = pos + 1;
            if (p < s.size() && (s[p] == QLatin1Char('+') || s[p] == QLatin1Char('-'))) ++p;
            if (p < s.size() && s[p].isDigit()) {
                while (p < s.size() && s[p].isDigit()) ++p;
                pos = p;
            }
        }
        if (pos - st == 1 && s[st] == QLatin1Char('0') && pos + 1 < s.size() && (s[pos] == QLatin1Char('x') || s[pos] == QLatin1Char('X')) && s[pos + 1].isLetterOrNumber())
            return fail(QStringLiteral("hexadecimal numbers are not supported"));
        QString t = s.mid(st, pos - st);
        t.remove(QLatin1Char(',')).remove(QLatin1Char('_'));
        bool ok = false;
        *out = t.toDouble(&ok);
        if (!ok) return fail(QStringLiteral("bad number"));
        return true;
    }
    bool apply(const QString &f, double a, double *out) {
        double r;
        if (f == QLatin1String("sqrt")) { if (a < 0) return fail(QStringLiteral("sqrt of a negative")); r = std::sqrt(a); }
        else if (f == QLatin1String("cbrt")) r = std::cbrt(a);
        else if (f == QLatin1String("abs")) r = std::fabs(a);
        else if (f == QLatin1String("sin")) r = std::sin(a);
        else if (f == QLatin1String("cos")) r = std::cos(a);
        else if (f == QLatin1String("tan")) r = std::tan(a);
        else if (f == QLatin1String("asin")) r = std::asin(a);
        else if (f == QLatin1String("acos")) r = std::acos(a);
        else if (f == QLatin1String("atan")) r = std::atan(a);
        else if (f == QLatin1String("ln")) { if (a <= 0) return fail(QStringLiteral("ln of a non positive")); r = std::log(a); }
        else if (f == QLatin1String("log") || f == QLatin1String("log10")) { if (a <= 0) return fail(QStringLiteral("log of a non positive")); r = std::log10(a); }
        else if (f == QLatin1String("log2")) { if (a <= 0) return fail(QStringLiteral("log of a non positive")); r = std::log2(a); }
        else if (f == QLatin1String("exp")) r = std::exp(a);
        else if (f == QLatin1String("floor")) r = std::floor(a);
        else if (f == QLatin1String("ceil")) r = std::ceil(a);
        else if (f == QLatin1String("round")) r = std::round(a);
        else return fail(QStringLiteral("unknown function %1").arg(f));
        *out = r;
        return true;
    }
};

}  // namespace

QString formatNumber(double v) {
    if (v == 0) return QStringLiteral("0");
    if (std::fabs(v) < 1e15 && std::fabs(v - std::round(v)) < 1e-9 * qMax(1.0, std::fabs(v))) return QString::number(qint64(std::llround(v)));
    QString t = QString::number(v, 'g', 12);
    return t;
}

CalcResult evaluate(const QString &input) {
    CalcResult r;
    QString in = input.trimmed();
    if (in.startsWith(QLatin1Char('='))) in = in.mid(1).trimmed();
    if (in.isEmpty()) { r.error = QStringLiteral("empty"); return r; }
    if (in.size() > 256) { r.error = QStringLiteral("too long"); return r; }
    Parser p;
    p.s = in;
    double v = 0;
    if (!p.expr(&v)) { r.error = p.err.isEmpty() ? QStringLiteral("cannot parse") : p.err; return r; }
    p.skip();
    if (p.pos < p.s.size()) { r.error = QStringLiteral("unexpected %1").arg(p.s[p.pos]); return r; }
    if (!std::isfinite(v)) { r.error = QStringLiteral("result is not a finite number"); return r; }
    r.ok = true;
    r.value = v;
    r.text = formatNumber(v);
    r.isExpression = p.sawOp;
    return r;
}

}  // namespace taos::search
