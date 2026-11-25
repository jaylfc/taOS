// SPDX-License-Identifier: AGPL-3.0-or-later
#include "articlehtml.h"

#include <QHash>
#include <QRegularExpression>
#include <QSet>
#include <QUrl>
#include <vector>

namespace Taos::Survival {

namespace {

const QSet<QString> &voidTags()
{
    static const QSet<QString> s{QStringLiteral("br"), QStringLiteral("hr"), QStringLiteral("img"), QStringLiteral("meta"),
        QStringLiteral("link"), QStringLiteral("input"), QStringLiteral("base"), QStringLiteral("area"),
        QStringLiteral("col"), QStringLiteral("source"), QStringLiteral("wbr"), QStringLiteral("param"),
        QStringLiteral("track"), QStringLiteral("embed"), QStringLiteral("keygen"), QStringLiteral("basefont"),
        QStringLiteral("frame")};
    return s;
}
// content is dropped with the tag
const QSet<QString> &dropWithContent()
{
    static const QSet<QString> s{QStringLiteral("script"), QStringLiteral("style"), QStringLiteral("noscript"),
        QStringLiteral("template"), QStringLiteral("iframe"), QStringLiteral("frameset"), QStringLiteral("object"),
        QStringLiteral("embed"), QStringLiteral("applet"), QStringLiteral("svg"), QStringLiteral("math"),
        QStringLiteral("canvas"), QStringLiteral("audio"), QStringLiteral("video"), QStringLiteral("form"),
        QStringLiteral("select"), QStringLiteral("button"), QStringLiteral("textarea"), QStringLiteral("head"),
        QStringLiteral("title"), QStringLiteral("nav"), QStringLiteral("map"), QStringLiteral("dialog"),
        QStringLiteral("link"), QStringLiteral("meta"), QStringLiteral("base")};
    return s;
}
// text content that must not be tokenised as markup
const QSet<QString> &rawText()
{
    static const QSet<QString> s{QStringLiteral("script"), QStringLiteral("style"), QStringLiteral("textarea"),
        QStringLiteral("title"), QStringLiteral("xmp"), QStringLiteral("noscript")};
    return s;
}
// tag -> the tag that is written (an empty string: the tag goes, its content stays)
const QHash<QString, QString> &tagMap()
{
    static const QHash<QString, QString> m{
        {QStringLiteral("p"), QStringLiteral("p")}, {QStringLiteral("div"), QStringLiteral("div")},
        {QStringLiteral("section"), QStringLiteral("div")}, {QStringLiteral("article"), QStringLiteral("div")},
        {QStringLiteral("main"), QStringLiteral("div")}, {QStringLiteral("header"), QStringLiteral("div")},
        {QStringLiteral("footer"), QStringLiteral("div")}, {QStringLiteral("aside"), QStringLiteral("div")},
        {QStringLiteral("figure"), QStringLiteral("div")}, {QStringLiteral("figcaption"), QStringLiteral("div")},
        {QStringLiteral("center"), QStringLiteral("div")}, {QStringLiteral("details"), QStringLiteral("div")},
        {QStringLiteral("summary"), QStringLiteral("div")}, {QStringLiteral("h1"), QStringLiteral("h1")},
        {QStringLiteral("h2"), QStringLiteral("h2")}, {QStringLiteral("h3"), QStringLiteral("h3")},
        {QStringLiteral("h4"), QStringLiteral("h4")}, {QStringLiteral("h5"), QStringLiteral("h5")},
        {QStringLiteral("h6"), QStringLiteral("h6")}, {QStringLiteral("b"), QStringLiteral("b")},
        {QStringLiteral("strong"), QStringLiteral("b")}, {QStringLiteral("i"), QStringLiteral("i")},
        {QStringLiteral("em"), QStringLiteral("i")}, {QStringLiteral("cite"), QStringLiteral("i")},
        {QStringLiteral("dfn"), QStringLiteral("i")}, {QStringLiteral("u"), QStringLiteral("u")},
        {QStringLiteral("ins"), QStringLiteral("u")}, {QStringLiteral("s"), QStringLiteral("s")},
        {QStringLiteral("strike"), QStringLiteral("s")}, {QStringLiteral("del"), QStringLiteral("s")},
        {QStringLiteral("sub"), QStringLiteral("sub")}, {QStringLiteral("sup"), QStringLiteral("sup")},
        {QStringLiteral("small"), QStringLiteral("small")}, {QStringLiteral("big"), QStringLiteral("big")},
        {QStringLiteral("ul"), QStringLiteral("ul")}, {QStringLiteral("ol"), QStringLiteral("ol")},
        {QStringLiteral("li"), QStringLiteral("li")}, {QStringLiteral("dl"), QStringLiteral("dl")},
        {QStringLiteral("dt"), QStringLiteral("dt")}, {QStringLiteral("dd"), QStringLiteral("dd")},
        {QStringLiteral("br"), QStringLiteral("br")}, {QStringLiteral("hr"), QStringLiteral("hr")},
        {QStringLiteral("a"), QStringLiteral("a")}, {QStringLiteral("img"), QStringLiteral("img")},
        {QStringLiteral("table"), QStringLiteral("table")}, {QStringLiteral("thead"), QStringLiteral("thead")},
        {QStringLiteral("tbody"), QStringLiteral("tbody")}, {QStringLiteral("tfoot"), QStringLiteral("tfoot")},
        {QStringLiteral("tr"), QStringLiteral("tr")}, {QStringLiteral("td"), QStringLiteral("td")},
        {QStringLiteral("th"), QStringLiteral("th")}, {QStringLiteral("caption"), QStringLiteral("p")},
        {QStringLiteral("blockquote"), QStringLiteral("blockquote")}, {QStringLiteral("pre"), QStringLiteral("pre")},
        {QStringLiteral("code"), QStringLiteral("code")}, {QStringLiteral("tt"), QStringLiteral("tt")},
        {QStringLiteral("kbd"), QStringLiteral("code")}, {QStringLiteral("samp"), QStringLiteral("code")}};
    return m;
}

struct Frame
{
    QString name;
    QString writeAs;   // the tag that was written, empty when none
    bool skip = false;
};

bool isSpace(QChar c) { return c == u' ' || c == u'\t' || c == u'\n' || c == u'\r' || c == u'\f'; }

struct Attr
{
    QString name, value;
};

QString normalised(const QString &v)
{
    // Browsers ignore tabs, newlines and other control characters inside an address, so "java\nscript:" is a scheme.
    QString out;
    for (QChar c : v)
        if (c.unicode() > 0x20 && c.unicode() != 0x7f && !(c.unicode() >= 0x80 && c.unicode() <= 0x9f))
            out.append(c);
    return out;
}

bool hasScheme(const QString &n)
{
    static const QRegularExpression re(QStringLiteral("^[A-Za-z][A-Za-z0-9+.\\-]*:"));
    return re.match(n).hasMatch();
}

bool hiddenByStyleOrClass(const QString &cls, const QString &style, bool hiddenAttr)
{
    if (hiddenAttr)
        return true;
    const QString st = normalised(style).toLower();
    if (st.contains(QStringLiteral("display:none")) || st.contains(QStringLiteral("visibility:hidden")))
        return true;
    static const QStringList junk{QStringLiteral("navbox"), QStringLiteral("mw-editsection"), QStringLiteral("noprint"),
        QStringLiteral("mw-empty-elt"), QStringLiteral("printfooter"), QStringLiteral("catlinks"),
        QStringLiteral("sistersitebox"), QStringLiteral("mw-jump-link"), QStringLiteral("vector-toc"),
        QStringLiteral("hidden")};
    for (const QString &c : cls.split(QLatin1Char(' '), Qt::SkipEmptyParts))
        if (junk.contains(c))
            return true;
    return false;
}

QString numberOnly(const QString &v, int maxValue)
{
    bool ok = false;
    QString t = v.trimmed();
    if (t.endsWith(QLatin1Char('%')) || t.endsWith(QStringLiteral("px")))
        return {};
    const int n = t.toInt(&ok);
    return ok && n > 0 && n <= maxValue ? QString::number(n) : QString();
}

} // namespace

QString decodeHtmlEntities(const QString &s)
{
    if (!s.contains(QLatin1Char('&')))
        return s;
    static const QHash<QString, QString> named{{QStringLiteral("amp"), QStringLiteral("&")}, {QStringLiteral("lt"), QStringLiteral("<")},
        {QStringLiteral("gt"), QStringLiteral(">")}, {QStringLiteral("quot"), QStringLiteral("\"")},
        {QStringLiteral("apos"), QStringLiteral("'")}, {QStringLiteral("nbsp"), QString(QChar(0xA0))},
        {QStringLiteral("ndash"), QString(QChar(0x2013))}, {QStringLiteral("mdash"), QString(QChar(0x2014))},
        {QStringLiteral("hellip"), QString(QChar(0x2026))}, {QStringLiteral("copy"), QString(QChar(0xA9))},
        {QStringLiteral("reg"), QString(QChar(0xAE))}, {QStringLiteral("deg"), QString(QChar(0xB0))},
        {QStringLiteral("plusmn"), QString(QChar(0xB1))}, {QStringLiteral("times"), QString(QChar(0xD7))},
        {QStringLiteral("middot"), QString(QChar(0xB7))}, {QStringLiteral("laquo"), QString(QChar(0xAB))},
        {QStringLiteral("raquo"), QString(QChar(0xBB))}, {QStringLiteral("lsquo"), QString(QChar(0x2018))},
        {QStringLiteral("rsquo"), QString(QChar(0x2019))}, {QStringLiteral("ldquo"), QString(QChar(0x201C))},
        {QStringLiteral("rdquo"), QString(QChar(0x201D))}, {QStringLiteral("bull"), QString(QChar(0x2022))},
        {QStringLiteral("minus"), QString(QChar(0x2212))}, {QStringLiteral("euro"), QString(QChar(0x20AC))},
        {QStringLiteral("pound"), QString(QChar(0xA3))}, {QStringLiteral("micro"), QString(QChar(0xB5))},
        {QStringLiteral("shy"), QString()}, {QStringLiteral("larr"), QString(QChar(0x2190))},
        {QStringLiteral("rarr"), QString(QChar(0x2192))}};
    QString out;
    out.reserve(s.size());
    for (qsizetype i = 0; i < s.size(); ++i) {
        if (s[i] != u'&') { out.append(s[i]); continue; }
        const qsizetype semi = s.indexOf(u';', i + 1);
        if (semi < 0 || semi - i > 12) { out.append(s[i]); continue; }
        const QString name = s.mid(i + 1, semi - i - 1);
        QString rep;
        bool ok = false;
        if (name.startsWith(u'#')) {
            const bool hex = name.size() > 1 && (name[1] == u'x' || name[1] == u'X');
            const uint cp = name.mid(hex ? 2 : 1).toUInt(&ok, hex ? 16 : 10);
            if (ok && cp > 0 && cp <= 0x10FFFF && !(cp >= 0xD800 && cp <= 0xDFFF))
                rep = QString::fromUcs4(reinterpret_cast<const char32_t *>(&cp), 1);
            else
                ok = false;
        } else if (named.contains(name)) {
            rep = named.value(name);
            ok = true;
        }
        if (ok) { out.append(rep); i = semi; } else { out.append(s[i]); }
    }
    return out;
}

QString resolvePackPath(const QString &articlePath, const QString &referenceIn)
{
    QString ref = decodeHtmlEntities(referenceIn);
    ref = normalised(ref);
    if (ref.isEmpty() || ref.startsWith(u'#') || ref.startsWith(u'?') || ref.startsWith(QStringLiteral("//")) || hasScheme(ref)
        || ref.contains(u'\\'))
        return {};
    const qsizetype cut = ref.indexOf(QRegularExpression(QStringLiteral("[?#]")));
    if (cut >= 0)
        ref.truncate(cut);
    if (ref.isEmpty())
        return {};
    ref = QUrl::fromPercentEncoding(ref.toUtf8());
    QStringList parts;
    if (!ref.startsWith(u'/')) {
        parts = articlePath.split(u'/');
        if (!parts.isEmpty())
            parts.removeLast();   // the article's own name
    }
    for (const QString &seg : ref.split(u'/')) {
        if (seg.isEmpty() || seg == QStringLiteral("."))
            continue;
        if (seg == QStringLiteral("..")) {
            if (!parts.isEmpty())   // like a browser: ".." above the root stays at the root (and a pack has no outside)
                parts.removeLast();
            continue;
        }
        parts.append(seg);
    }
    return parts.join(u'/');
}

QString packLinkUrl(const QString &packId, const QString &path)
{
    return QStringLiteral("zim:") + QString::fromLatin1(QUrl::toPercentEncoding(packId)) + QLatin1Char('/')
        + QString::fromLatin1(QUrl::toPercentEncoding(path, "/"));
}
QString packImageUrl(const QString &packId, const QString &path)
{
    return QStringLiteral("image://zimimg/") + QString::fromLatin1(QUrl::toPercentEncoding(packId)) + QLatin1Char('/')
        + QString::fromLatin1(QUrl::toPercentEncoding(path, "/"));
}

SanitizedArticle sanitizeArticle(const QByteArray &htmlBytes, const QString &packId, const QString &articlePath)
{
    SanitizedArticle res;
    const QString in = QString::fromUtf8(htmlBytes);
    QString out;
    std::vector<Frame> stack;
    int skipDepth = 0;
    bool inPre = false;
    QString firstHeading;
    bool capturingHeading = false;
    QString titleText;

    auto esc = [](const QString &t) { return t.toHtmlEscaped(); };
    auto closeFrameTo = [&](int idx) {
        while (int(stack.size()) > idx) {
            Frame f = stack.back();
            stack.pop_back();
            if (f.skip)
                --skipDepth;
            if (!f.writeAs.isEmpty() && skipDepth == 0) {
                out += QStringLiteral("</") + f.writeAs + QLatin1Char('>');
                if (f.writeAs == QStringLiteral("pre"))
                    inPre = false;
                if (capturingHeading && f.writeAs.size() == 2 && f.writeAs[0] == u'h')
                    capturingHeading = false;
            }
        }
    };

    const qsizetype n = in.size();
    qsizetype i = 0;
    while (i < n) {
        if (out.size() > kMaxArticleHtml) {
            res.truncated = true;
            break;
        }
        if (in[i] != u'<') {
            const qsizetype next = in.indexOf(u'<', i);
            const qsizetype end = next < 0 ? n : next;
            if (skipDepth == 0) {
                QString t = decodeHtmlEntities(in.mid(i, end - i));
                if (capturingHeading && firstHeading.size() < 200)
                    firstHeading += t;
                out += esc(t);
            }
            i = end;
            continue;
        }
        // comments, doctype, processing instructions
        if (in.mid(i, 4) == QStringLiteral("<!--")) {
            const qsizetype e = in.indexOf(QStringLiteral("-->"), i + 4);
            i = e < 0 ? n : e + 3;
            continue;
        }
        if (i + 1 < n && (in[i + 1] == u'!' || in[i + 1] == u'?')) {
            const qsizetype e = in.indexOf(u'>', i + 2);
            i = e < 0 ? n : e + 1;
            continue;
        }
        const bool closing = i + 1 < n && in[i + 1] == u'/';
        qsizetype p = i + (closing ? 2 : 1);
        if (p >= n || !in[p].isLetter()) {   // a lone "<" is text
            if (skipDepth == 0)
                out += QStringLiteral("&lt;");
            ++i;
            continue;
        }
        qsizetype ns = p;
        while (p < n && !isSpace(in[p]) && in[p] != u'>' && in[p] != u'/')
            ++p;
        const QString name = in.mid(ns, p - ns).toLower();
        // attributes
        QList<Attr> attrs;
        bool selfClose = false;
        while (p < n && in[p] != u'>') {
            if (isSpace(in[p])) { ++p; continue; }
            if (in[p] == u'/') { selfClose = true; ++p; continue; }
            selfClose = false;
            qsizetype as = p;
            while (p < n && !isSpace(in[p]) && in[p] != u'=' && in[p] != u'>' && in[p] != u'/')
                ++p;
            Attr a;
            a.name = in.mid(as, p - as).toLower();
            while (p < n && isSpace(in[p])) ++p;
            if (p < n && in[p] == u'=') {
                ++p;
                while (p < n && isSpace(in[p])) ++p;
                if (p < n && (in[p] == u'"' || in[p] == u'\'')) {
                    const QChar q = in[p++];
                    const qsizetype ve = in.indexOf(q, p);
                    const qsizetype stop = ve < 0 ? n : ve;
                    a.value = in.mid(p, stop - p);
                    p = ve < 0 ? n : ve + 1;
                } else {
                    qsizetype vs = p;
                    while (p < n && !isSpace(in[p]) && in[p] != u'>')
                        ++p;
                    a.value = in.mid(vs, p - vs);
                }
            }
            if (!a.name.isEmpty())
                attrs.append(a);
        }
        i = p < n ? p + 1 : n;
        auto attr = [&attrs](const char *k) {
            for (const Attr &a : attrs)
                if (a.name == QLatin1String(k))
                    return decodeHtmlEntities(a.value);
            return QString();
        };
        auto hasAttr = [&attrs](const char *k) {
            for (const Attr &a : attrs)
                if (a.name == QLatin1String(k))
                    return true;
            return false;
        };

        if (closing) {
            // close the nearest open element of this name
            int idx = -1;
            for (int k = int(stack.size()) - 1; k >= 0; --k)
                if (stack[size_t(k)].name == name) { idx = k; break; }
            if (idx >= 0)
                closeFrameTo(idx);
            continue;
        }

        if (rawText().contains(name) && !selfClose) {
            // the raw text up to the end tag is never markup
            const QRegularExpression endRe(QStringLiteral("</%1\\s*>").arg(name), QRegularExpression::CaseInsensitiveOption);
            const QRegularExpressionMatch m = endRe.match(in, i);
            const qsizetype ts = i;
            const qsizetype te = m.hasMatch() ? m.capturedStart() : n;
            if (name == QStringLiteral("title"))
                titleText = decodeHtmlEntities(in.mid(ts, te - ts)).simplified();
            i = m.hasMatch() ? m.capturedEnd() : n;
            continue;
        }

        const bool isVoid = voidTags().contains(name);
        const bool hidden = hiddenByStyleOrClass(attr("class"), attr("style"), hasAttr("hidden"));
        const bool drop = dropWithContent().contains(name) || hidden;

        if (drop) {
            if (!isVoid && !selfClose) {
                stack.push_back({name, QString(), true});
                ++skipDepth;
            }
            continue;
        }
        if (skipDepth > 0) {
            if (!isVoid && !selfClose)
                stack.push_back({name, QString(), false});
            continue;
        }

        QString write = tagMap().value(name);
        QString tag;
        if (write == QStringLiteral("a")) {
            const QString target = resolvePackPath(articlePath, attr("href"));
            const QString raw = normalised(decodeHtmlEntities(attr("href")));
            if (!target.isEmpty()) {
                tag = QStringLiteral("<a href=\"") + esc(packLinkUrl(packId, target)) + QStringLiteral("\">");
                res.links.append(target);
            } else {
                write.clear();   // anchors, remote links and scripts: the text stays, the link goes
                if (!raw.isEmpty() && !raw.startsWith(u'#'))
                    ++res.blockedRemote;
            }
        } else if (write == QStringLiteral("img")) {
            const QString target = resolvePackPath(articlePath, attr("src"));
            if (target.isEmpty()) {
                if (!normalised(attr("src")).isEmpty())
                    ++res.blockedRemote;
                continue;
            }
            tag = QStringLiteral("<img src=\"") + esc(packImageUrl(packId, target)) + QLatin1Char('"');
            const QString w = numberOnly(attr("width"), 4000), h = numberOnly(attr("height"), 4000);
            if (!w.isEmpty() && !h.isEmpty())
                tag += QStringLiteral(" width=\"") + w + QStringLiteral("\" height=\"") + h + QLatin1Char('"');
            tag += QStringLiteral(">");
            out += tag;
            res.images.append(target);
            continue;
        } else if (write == QStringLiteral("br") || write == QStringLiteral("hr")) {
            out += QStringLiteral("<") + write + QStringLiteral(">");
            continue;
        } else if (write == QStringLiteral("table")) {
            tag = QStringLiteral("<table border=\"0\" cellspacing=\"0\" cellpadding=\"5\">");
        } else if (write == QStringLiteral("td") || write == QStringLiteral("th")) {
            tag = QStringLiteral("<") + write;
            const QString cs = numberOnly(attr("colspan"), 100), rs = numberOnly(attr("rowspan"), 100);
            if (!cs.isEmpty()) tag += QStringLiteral(" colspan=\"") + cs + QLatin1Char('"');
            if (!rs.isEmpty()) tag += QStringLiteral(" rowspan=\"") + rs + QLatin1Char('"');
            tag += QStringLiteral(">");
        } else if (write == QStringLiteral("ol")) {
            tag = QStringLiteral("<ol>");
        } else if (!write.isEmpty()) {
            tag = QStringLiteral("<") + write + QStringLiteral(">");
        }
        if (isVoid || selfClose) {
            continue;   // nothing left to close (a self-closing known tag is written as empty by the browsers, too)
        }
        if (!write.isEmpty()) {
            out += tag;
            if (write == QStringLiteral("pre"))
                inPre = true;
            if (write.size() == 2 && write[0] == u'h' && write[1].isDigit() && firstHeading.isEmpty())
                capturingHeading = true;
        }
        stack.push_back({name, write, false});
    }
    closeFrameTo(0);
    (void)inPre;
    res.html = out;
    res.title = !titleText.isEmpty() ? titleText : firstHeading.simplified();
    return res;
}

} // namespace Taos::Survival
