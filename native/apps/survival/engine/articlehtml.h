// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Turns the HTML of a ZIM article into the small subset of HTML that Qt's rich text shows, with no web view.
// It is a whitelist that REBUILDS the document: only known tags are written, only known attributes of them, every
// text and value is escaped again. Scripts, styles, frames, forms, SVG and every remote address are gone; links and
// images that stay point into the same pack (zim: and image://zimimg/ addresses).
#include <QByteArray>
#include <QString>
#include <QStringList>

namespace Taos::Survival {

struct SanitizedArticle
{
    QString html;             // a body fragment: the caller wraps it with the theme's style sheet
    QString title;            // the document's <title> or its first heading, plain text
    QStringList images;       // pack paths of the images that were kept
    QStringList links;        // pack paths of the links that were kept
    int blockedRemote = 0;    // remote or non-pack links and images that were dropped
    bool truncated = false;   // the article was cut at the size or time limit: show "too large" instead
};

// What the reader's rich text can lay out without freezing the window: output beyond this, or more than
// kMaxElementDepth nested elements, or more than kMaxTableDepth nested tables, is cut or flattened.
constexpr qsizetype kMaxArticleHtml = 1536 * 1024;
constexpr int kMaxElementDepth = 32;
constexpr int kMaxTableDepth = 3;
constexpr int kSanitizeBudgetMs = 3000;

/// `articlePath` is where the article lives in the pack (relative links resolve against it), `packId` names the pack in
/// the addresses that are written.
SanitizedArticle sanitizeArticle(const QByteArray &html, const QString &packId, const QString &articlePath);

/// A relative reference resolved against the path of the article that holds it: "../I/x.png" from "A/Foo" is "I/x.png".
/// Query and fragment are cut; ".." above the root stays at the root, as in a browser. Returns an empty string for
/// anything that is not a plain relative path (a scheme, a protocol-relative address, a backslash).
QString resolvePackPath(const QString &articlePath, const QString &reference);

/// HTML character references decoded (numeric and the common named ones); unknown names are kept as written.
QString decodeHtmlEntities(const QString &s);

/// The address a reader shows for a pack path, and the image provider's address for one.
QString packLinkUrl(const QString &packId, const QString &path);
QString packImageUrl(const QString &packId, const QString &path);

} // namespace Taos::Survival
