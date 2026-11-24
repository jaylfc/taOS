#pragma once
// Pure logic of the Messages app: ports of the web app's small pure modules
// (MessagesApp.agentSections.ts, MessagesApp.stallWatch.ts, index.tsx helpers) and
// the markdown subset. No QML, no network: unit tested on their own.
#include <QDateTime>
#include <QJsonArray>
#include <QJsonObject>
#include <QSet>
#include <QString>
#include <QStringList>
#include <QVariantList>

namespace Taos::Messages {

/// `toMs` of chat/time.ts: a number under 1e12 is seconds.
qint64 toMs(double ts);

/// "now", "Nm" under an hour, then the clock time (`relativeTime` of index.tsx).
QString relativeTime(double ts, qint64 nowMs);
/// The clock time, "14:05".
QString clockTime(double ts);
/// "Today", "Yesterday", or "Wed 12 Nov" (`dayLabel` of index.tsx, local days).
QString dayLabel(double ts, const QDateTime &now = QDateTime::currentDateTime());
/// Tooltip and accessible form: "Wed 12 Nov, 14:05:09".
QString fullTime(double ts);

/// Run grouping of MessageList.tsx: `b` starts a new run when the author or type
/// differs, the local day differs, or the gap is over five minutes.
bool breaksRun(const QString &authorA, const QString &typeA, double tsA,
               const QString &authorB, const QString &typeB, double tsB);

/// Where an agent DM sits (`bucketAgentChannels`, MessagesApp.agentSections.ts).
enum class Bucket { Live, Suspended, Archived, NonAgent };

struct AgentInfo
{
    QString name;
    QString displayName;
    QString status;
    QString emoji;
};

Bucket bucketFor(const QString &channelName, const QStringList &members, const QString &settingsKind,
                 const QList<AgentInfo> &live, const QSet<QString> &archivedKeys, bool listsLoaded);

/// The sidebar section of a channel: "Channels", "Agents-DMs", "Direct Messages" or
/// "Remote" (`SECTIONS` in index.tsx:2259).
QString sectionFor(const QString &type, Bucket bucket);
int sectionOrder(const QString &section);

/// `computeAgentPresence`: "working", "live" or "idle".
QString presenceFor(const QString &status, bool working);

/// Mention candidates (index.tsx:2186): channel members except `user` plus `all`,
/// prefix matches first, then substring matches, at most six.
QStringList mentionCandidates(const QStringList &members, const QString &partial);
/// The `@partial` at the cursor, or atIndex -1 (`handleInputChange`, index.tsx:1927).
struct MentionAt
{
    int atIndex = -1;
    QString partial;
};
MentionAt mentionAt(const QString &text, int cursor);

/// Stall watch (MessagesApp.stallWatch.ts).
QString pickWatchAgent(const QString &channelType, const QStringList &members, const QString &text,
                       const QStringList &agentNames);
constexpr qint64 kStallHintMs = 20000;
constexpr qint64 kStallWarnMs = 75000;

/// Markdown subset to the HTML subset Qt rich text renders. Everything is escaped;
/// only b, i, s, code, a (http, https, mailto), ul, ol, li, blockquote, br, table
/// are produced. Images are never produced.
QString markdownToHtml(const QString &md);
/// Splits ```fenced``` code out: [{kind:"html", html}, {kind:"code", text, lang}].
QVariantList segments(const QString &content);
/// True for a message the transcript should collapse.
bool isLongText(const QString &content);

QString escapeHtml(const QString &s);

} // namespace Taos::Messages
