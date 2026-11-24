#include "chatlogic.h"

#include <QLocale>
#include <QRegularExpression>
#include <QUrl>
#include <QVariantMap>
#include <cmath>

using namespace Qt::StringLiterals;

namespace Taos::Messages {

qint64 toMs(double ts)
{
    return ts < 1e12 ? qint64(std::llround(ts * 1000.0)) : qint64(ts);
}

QString clockTime(double ts)
{
    return QDateTime::fromMSecsSinceEpoch(toMs(ts)).time().toString(u"HH:mm"_s);
}

QString relativeTime(double ts, qint64 nowMs)
{
    const qint64 mins = (nowMs - toMs(ts)) / 60000;
    if (mins < 1)
        return u"now"_s;
    if (mins < 60)
        return QString::number(mins) + u"m"_s;
    return clockTime(ts);
}

QString dayLabel(double ts, const QDateTime &now)
{
    const QDate d = QDateTime::fromMSecsSinceEpoch(toMs(ts)).date();
    const qint64 diff = d.daysTo(now.date());
    if (diff == 0)
        return u"Today"_s;
    if (diff == 1)
        return u"Yesterday"_s;
    return QLocale::c().toString(d, u"ddd d MMM"_s);
}

QString fullTime(double ts)
{
    const QDateTime t = QDateTime::fromMSecsSinceEpoch(toMs(ts));
    return QLocale::c().toString(t.date(), u"ddd d MMM"_s) + u", "_s + t.time().toString(u"HH:mm:ss"_s);
}

bool breaksRun(const QString &authorA, const QString &typeA, double tsA, const QString &authorB,
               const QString &typeB, double tsB)
{
    if (authorA != authorB || typeA != typeB)
        return true;
    const qint64 a = toMs(tsA), b = toMs(tsB);
    if (QDateTime::fromMSecsSinceEpoch(a).date() != QDateTime::fromMSecsSinceEpoch(b).date())
        return true;
    return std::llabs(b - a) > 5 * 60 * 1000;
}

Bucket bucketFor(const QString &channelName, const QStringList &members, const QString &settingsKind,
                 const QList<AgentInfo> &live, const QSet<QString> &archivedKeys, bool listsLoaded)
{
    if (settingsKind == u"a2a"_s)
        return Bucket::NonAgent;
    QStringList keys{channelName};
    QString agentMember;
    for (const QString &m : members)
        if (m != u"user"_s) {
            agentMember = m;
            break;
        }
    if (!agentMember.isEmpty())
        keys << agentMember;
    for (const QString &k : keys)
        for (const AgentInfo &a : live)
            if (a.name == k || (!a.displayName.isEmpty() && a.displayName == k)) {
                if (a.status == u"running"_s)
                    return Bucket::Live;
                if (a.status == u"paused"_s)
                    return Bucket::Suspended;
                return Bucket::Archived;
            }
    for (const QString &k : keys)
        if (archivedKeys.contains(k))
            return Bucket::Archived;
    if (!agentMember.isEmpty() && listsLoaded)
        return Bucket::Archived;
    return Bucket::NonAgent;
}

QString sectionFor(const QString &type, Bucket bucket)
{
    if (type == u"topic"_s || type == u"group"_s)
        return u"Channels"_s;
    if (type == u"dm-remote"_s)
        return u"Remote"_s;
    return bucket == Bucket::NonAgent ? u"Direct Messages"_s : u"Agents-DMs"_s;
}

int sectionOrder(const QString &section)
{
    if (section == u"Channels"_s)
        return 0;
    if (section == u"Agents-DMs"_s)
        return 1;
    if (section == u"Direct Messages"_s)
        return 2;
    return 3;
}

QString presenceFor(const QString &status, bool working)
{
    if (working)
        return u"working"_s;
    return status == u"running"_s ? u"live"_s : u"idle"_s;
}

QStringList mentionCandidates(const QStringList &members, const QString &partial)
{
    const QString q = partial.toLower();
    QStringList pool;
    for (const QString &m : members)
        if (m != u"user"_s)
            pool << m;
    pool << u"all"_s;
    QStringList pref, sub;
    for (const QString &m : std::as_const(pool)) {
        const QString l = m.toLower();
        if (l.startsWith(q))
            pref << m;
        else if (l.contains(q))
            sub << m;
    }
    QStringList out = pref + sub;
    while (out.size() > 6)
        out.removeLast();
    return out;
}

MentionAt mentionAt(const QString &text, int cursor)
{
    const QString head = text.left(cursor);
    static const QRegularExpression re(u"(?:^|\\s)@([^\\s@]*)$"_s);
    const auto m = re.match(head);
    if (!m.hasMatch())
        return {};
    const QString part = m.captured(1);
    return {int(cursor - part.size() - 1), part};
}

QString pickWatchAgent(const QString &channelType, const QStringList &members, const QString &text,
                       const QStringList &agentNames)
{
    QStringList agents;
    for (const QString &m : members)
        if (m != u"user"_s && agentNames.contains(m))
            agents << m;
    if (channelType == u"dm"_s)
        return agents.value(0);
    for (const QString &a : std::as_const(agents))
        if (text.contains(u"@"_s + a))
            return a;
    return {};
}

QString escapeHtml(const QString &s)
{
    QString o;
    o.reserve(s.size() + 16);
    for (const QChar c : s) {
        switch (c.unicode()) {
        case '&': o += u"&amp;"_s; break;
        case '<': o += u"&lt;"_s; break;
        case '>': o += u"&gt;"_s; break;
        case '"': o += u"&quot;"_s; break;
        default: o += c;
        }
    }
    return o;
}

namespace {

bool safeUrl(const QString &raw)
{
    const QUrl u(raw);
    const QString s = u.scheme().toLower();
    return u.isValid() && (s == u"http"_s || s == u"https"_s || s == u"mailto"_s);
}

// Inline markdown on text that is NOT yet escaped.
QString inlineHtml(const QString &src)
{
    // 1. pull out code spans so nothing inside them is touched
    QStringList codes;
    QString t;
    int i = 0;
    while (i < src.size()) {
        if (src[i] == u'`') {
            const int end = src.indexOf(u'`', i + 1);
            if (end > i + 1) {
                codes << src.mid(i + 1, end - i - 1);
                t += QChar(char16_t(0xE000 + codes.size() - 1));
                i = end + 1;
                continue;
            }
        }
        t += src[i++];
    }
    // 2. links [text](url), before escaping so the url survives; placeholders again
    QStringList links;
    static const QRegularExpression linkRe(u"\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)"_s);
    QString t2;
    int pos = 0;
    auto it = linkRe.globalMatch(t);
    while (it.hasNext()) {
        const auto m = it.next();
        t2 += t.mid(pos, m.capturedStart() - pos);
        if (safeUrl(m.captured(2))) {
            links << (u"<a href=\""_s + escapeHtml(m.captured(2)) + u"\">"_s + escapeHtml(m.captured(1)) + u"</a>"_s);
            t2 += QChar(char16_t(0xF000 + links.size() - 1));
        } else {
            t2 += m.captured(0);
        }
        pos = m.capturedEnd();
    }
    t2 += t.mid(pos);
    // 3. bare urls
    static const QRegularExpression urlRe(u"(https?://[^\\s<>\"]+[^\\s<>\".,;:!?)\\]])"_s);
    QString t3;
    pos = 0;
    it = urlRe.globalMatch(t2);
    while (it.hasNext()) {
        const auto m = it.next();
        t3 += t2.mid(pos, m.capturedStart() - pos);
        links << (u"<a href=\""_s + escapeHtml(m.captured(1)) + u"\">"_s + escapeHtml(m.captured(1)) + u"</a>"_s);
        t3 += QChar(char16_t(0xF000 + links.size() - 1));
        pos = m.capturedEnd();
    }
    t3 += t2.mid(pos);
    // 4. escape, then emphasis
    QString h = escapeHtml(t3);
    static const QRegularExpression bold(u"(\\*\\*|__)(?=\\S)(.+?)(?<=\\S)\\1"_s);
    static const QRegularExpression strike(u"~~(?=\\S)(.+?)(?<=\\S)~~"_s);
    static const QRegularExpression ital(u"(?<![\\w*])\\*(?=[^\\s*])(.+?)(?<=[^\\s*])\\*(?![\\w*])"_s);
    static const QRegularExpression ital2(u"(?<![\\w_])_(?=[^\\s_])(.+?)(?<=[^\\s_])_(?![\\w_])"_s);
    h.replace(bold, u"<b>\\2</b>"_s);
    h.replace(strike, u"<s>\\1</s>"_s);
    h.replace(ital, u"<i>\\1</i>"_s);
    h.replace(ital2, u"<i>\\1</i>"_s);
    // 5. put the placeholders back
    for (int k = 0; k < links.size(); ++k)
        h.replace(QChar(char16_t(0xF000 + k)), links[k]);
    for (int k = 0; k < codes.size(); ++k)
        h.replace(QChar(char16_t(0xE000 + k)), u"<code>"_s + escapeHtml(codes[k]) + u"</code>"_s);
    return h;
}

} // namespace

QString markdownToHtml(const QString &md)
{
    const QStringList lines = md.split(u'\n');
    QString out;
    QStringList para;
    enum { None, Ul, Ol, Quote, Table } mode = None;
    auto flushPara = [&] {
        if (!para.isEmpty()) {
            out += u"<p style=\"margin:0 0 4px 0\">"_s + para.join(u"<br>"_s) + u"</p>"_s;
            para.clear();
        }
    };
    auto closeMode = [&] {
        if (mode == Ul) out += u"</ul>"_s;
        else if (mode == Ol) out += u"</ol>"_s;
        else if (mode == Quote) out += u"</blockquote>"_s;
        else if (mode == Table) out += u"</table>"_s;
        mode = None;
    };
    static const QRegularExpression ulRe(u"^\\s*[-*+]\\s+(.*)$"_s);
    static const QRegularExpression olRe(u"^\\s*\\d+[.)]\\s+(.*)$"_s);
    static const QRegularExpression qRe(u"^\\s*>\\s?(.*)$"_s);
    static const QRegularExpression hRe(u"^\\s*#{1,6}\\s+(.*)$"_s);
    static const QRegularExpression sepRe(u"^\\s*\\|?\\s*:?-{2,}:?\\s*(\\|\\s*:?-{2,}:?\\s*)*\\|?\\s*$"_s);
    for (const QString &raw : lines) {
        const QString line = raw.endsWith(u'\r') ? raw.chopped(1) : raw;
        if (line.trimmed().isEmpty()) {
            flushPara();
            closeMode();
            continue;
        }
        QRegularExpressionMatch m;
        if ((m = ulRe.match(line)).hasMatch()) {
            flushPara();
            if (mode != Ul) { closeMode(); out += u"<ul>"_s; mode = Ul; }
            out += u"<li>"_s + inlineHtml(m.captured(1)) + u"</li>"_s;
        } else if ((m = olRe.match(line)).hasMatch()) {
            flushPara();
            if (mode != Ol) { closeMode(); out += u"<ol>"_s; mode = Ol; }
            out += u"<li>"_s + inlineHtml(m.captured(1)) + u"</li>"_s;
        } else if ((m = qRe.match(line)).hasMatch()) {
            flushPara();
            if (mode != Quote) { closeMode(); out += u"<blockquote>"_s; mode = Quote; }
            out += inlineHtml(m.captured(1)) + u"<br>"_s;
        } else if ((m = hRe.match(line)).hasMatch()) {
            flushPara();
            closeMode();
            out += u"<p style=\"margin:4px 0 2px 0\"><b>"_s + inlineHtml(m.captured(1)) + u"</b></p>"_s;
        } else if (line.trimmed().startsWith(u'|') && line.count(u'|') >= 2) {
            flushPara();
            if (sepRe.match(line).hasMatch())
                continue;
            if (mode != Table) { closeMode(); out += u"<table cellspacing=\"0\" cellpadding=\"3\">"_s; mode = Table; }
            QString trimmed = line.trimmed();
            if (trimmed.startsWith(u'|')) trimmed.remove(0, 1);
            if (trimmed.endsWith(u'|')) trimmed.chop(1);
            out += u"<tr>"_s;
            for (const QString &cell : trimmed.split(u'|'))
                out += u"<td>"_s + inlineHtml(cell.trimmed()) + u"</td>"_s;
            out += u"</tr>"_s;
        } else {
            closeMode();
            para << inlineHtml(line);
        }
    }
    flushPara();
    closeMode();
    return out;
}

QVariantList segments(const QString &content)
{
    QVariantList out;
    static const QRegularExpression fence(u"```([^\\n`]*)\\n([\\s\\S]*?)```"_s);
    int pos = 0;
    auto addText = [&](const QString &t) {
        if (t.trimmed().isEmpty())
            return;
        out << QVariantMap{{u"kind"_s, u"html"_s}, {u"html"_s, markdownToHtml(t)}};
    };
    auto it = fence.globalMatch(content);
    while (it.hasNext()) {
        const auto m = it.next();
        addText(content.mid(pos, m.capturedStart() - pos));
        QString code = m.captured(2);
        if (code.endsWith(u'\n'))
            code.chop(1);
        out << QVariantMap{{u"kind"_s, u"code"_s}, {u"text"_s, code}, {u"lang"_s, m.captured(1).trimmed()}};
        pos = m.capturedEnd();
    }
    // An unterminated fence (a streaming reply) shows the rest as code.
    const QString rest = content.mid(pos);
    const int open = rest.indexOf(u"```"_s);
    if (open >= 0) {
        addText(rest.left(open));
        QString code = rest.mid(open + 3);
        const int nl = code.indexOf(u'\n');
        const QString lang = nl >= 0 ? code.left(nl).trimmed() : code.trimmed();
        code = nl >= 0 ? code.mid(nl + 1) : QString();
        out << QVariantMap{{u"kind"_s, u"code"_s}, {u"text"_s, code}, {u"lang"_s, lang}};
    } else {
        addText(rest);
    }
    return out;
}

bool isLongText(const QString &content)
{
    return content.size() > 1800 || content.count(u'\n') > 24;
}

} // namespace Taos::Messages
