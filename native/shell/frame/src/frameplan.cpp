// SPDX-License-Identifier: AGPL-3.0-or-later
#include "frameplan.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <algorithm>

const FrameWindow *FrameTree::find(int id) const
{
    for (const FrameWindow &w : windows)
        if (w.id == id)
            return &w;
    return nullptr;
}

static QRect rectOf(const QJsonValue &v)
{
    const QJsonObject o = v.toObject();
    return QRect(o.value(QLatin1String("x")).toInt(), o.value(QLatin1String("y")).toInt(),
                 o.value(QLatin1String("width")).toInt(), o.value(QLatin1String("height")).toInt());
}

namespace {
struct Walk {
    QVector<FrameWindow> out;
    QString output;
    QRect outputRect;
    QString workspace;
    bool scratch = false;

    void leaf(const QJsonObject &n, bool floating, bool inTabs, QVector<FrameWindow> *dst)
    {
        FrameWindow w;
        w.inTabs = inTabs;
        w.id = n.value(QLatin1String("id")).toInt();
        w.appId = n.value(QLatin1String("app_id")).toString();
        if (w.appId.isEmpty())
            w.appId = n.value(QLatin1String("window_properties")).toObject().value(QLatin1String("class")).toString();
        w.title = n.value(QLatin1String("name")).toString();
        w.pid = n.value(QLatin1String("pid")).toInt();
        w.focused = n.value(QLatin1String("focused")).toBool();
        w.floating = floating;
        w.fullscreen = n.value(QLatin1String("fullscreen_mode")).toInt() != 0;
        w.visible = n.value(QLatin1String("visible")).toBool();
        w.border = n.value(QLatin1String("border")).toString();
        w.rect = rectOf(n.value(QLatin1String("rect")));
        w.deco = rectOf(n.value(QLatin1String("deco_rect")));
        // deco_rect is relative to the workspace (measured on sway 1.10 with a bar reserving 33 px at the top: a tiled
        // window at y 64 reports deco y 0, a floating one at 300 reports 267) while rect is absolute. The bar is
        // directly above the content rectangle, so only the height is taken from deco_rect. (Tabbed and stacked
        // containers are not supported: see inTabs.)
        if (!w.deco.isEmpty() && w.deco.height() > 0)
            w.deco = QRect(w.rect.x(), w.rect.y() - w.deco.height(), w.rect.width(), w.deco.height());
        w.parked = scratch;
        for (const QJsonValue &m : n.value(QLatin1String("marks")).toArray())
            if (m.toString().startsWith(QLatin1String("taos_min_")))
                w.minimisedMark = true;
        w.output = scratch ? QString() : output;
        w.outputRect = outputRect;
        w.workspace = workspace;
        dst->append(w);
    }

    // sway describes a window (a view) with a "shell" member; split containers have none.
    void gather(const QJsonObject &n, bool floating, bool inTabs, QVector<FrameWindow> *tiled, QVector<FrameWindow> *floats)
    {
        if (n.contains(QLatin1String("shell"))) {
            leaf(n, floating, inTabs, floating ? floats : tiled);
            return;
        }
        const QString layout = n.value(QLatin1String("layout")).toString();
        const bool tabs = layout == QLatin1String("tabbed") || layout == QLatin1String("stacked");
        for (const QJsonValue &k : n.value(QLatin1String("nodes")).toArray())
            gather(k.toObject(), floating, tabs, tiled, floats);
        for (const QJsonValue &k : n.value(QLatin1String("floating_nodes")).toArray())
            gather(k.toObject(), true, false, tiled, floats);
    }
};
}  // namespace

bool parseFrameTree(const QByteArray &json, FrameTree *out, QString *error)
{
    out->windows.clear();
    QJsonParseError pe;
    const QJsonDocument d = QJsonDocument::fromJson(json, &pe);
    if (!d.isObject()) {
        if (error) *error = QStringLiteral("not a sway tree: %1").arg(pe.errorString());
        return false;
    }
    Walk w;
    for (const QJsonValue &ov : d.object().value(QLatin1String("nodes")).toArray()) {
        const QJsonObject o = ov.toObject();
        w.output = o.value(QLatin1String("name")).toString();
        w.outputRect = rectOf(o.value(QLatin1String("rect")));
        w.scratch = w.output == QLatin1String("__i3");
        for (const QJsonValue &wv : o.value(QLatin1String("nodes")).toArray()) {
            const QJsonObject ws = wv.toObject();
            w.workspace = ws.value(QLatin1String("name")).toString();
            QVector<FrameWindow> tiled, floats;
            w.gather(ws, false, false, &tiled, &floats);
            w.out += tiled;
            w.out += floats;
        }
    }
    out->windows = w.out;
    return true;
}

// ----------------------------------------------------------------------------------------------- layout

QSize StripLayout::surfaceSize()
{
    return QSize(3 * kHit + 2 * kGap, kHit);
}

QVector<StripLayout::Button> StripLayout::buttons(bool right)
{
    const QStringList order = right ? QStringList{QStringLiteral("minimise"), QStringLiteral("full"), QStringLiteral("close")}
                                    : QStringList{QStringLiteral("close"), QStringLiteral("minimise"), QStringLiteral("full")};
    QVector<Button> v;
    for (int i = 0; i < order.size(); ++i)
        v.append({order.at(i), QRect(i * (kHit + kGap), 0, kHit, kHit)});
    return v;
}

QString StripLayout::buttonAt(bool right, const QPoint &p)
{
    for (const Button &b : buttons(right))
        if (b.rect.contains(p))
            return b.name;
    return QString();
}

QString StripLayout::labelFor(const QString &button, const QString &t)
{
    const QString what = button == QLatin1String("close") ? QStringLiteral("Close")
                       : button == QLatin1String("minimise") ? QStringLiteral("Minimise") : QStringLiteral("Full size");
    return t.isEmpty() ? what + QStringLiteral(" window") : what + QLatin1Char(' ') + t;
}

// ----------------------------------------------------------------------------------------------- planning

QVector<Strip> planStrips(const FrameTree &tree, const FrameConfig &cfg, const PlanInput &in)
{
    QVector<Strip> strips;
    const auto &ws = tree.windows;
    for (int i = 0; i < ws.size(); ++i) {
        const FrameWindow &w = ws.at(i);
        if (!cfg.shouldFrame(w.appId))
            continue;
        Strip s;
        s.id = w.id;
        s.title = w.title;
        s.appId = w.appId;
        s.output = w.output;
        s.focused = w.focused;
        s.right = in.buttonsRight;
        s.size = StripLayout::surfaceSize();
        const int x = in.buttonsRight ? w.deco.right() + 1 - StripLayout::kInset - s.size.width() : w.deco.x() + StripLayout::kInset;
        const int y = w.deco.y() + (w.deco.height() - s.size.height()) / 2;
        s.pos = QPoint(x, y);
        s.margin = s.pos - w.outputRect.topLeft();
        if (w.parked) s.reason = QStringLiteral("minimised");
        else if (w.fullscreen) s.reason = QStringLiteral("fullscreen");
        else if (!w.visible) s.reason = QStringLiteral("hidden");
        else if (w.inTabs) s.reason = QStringLiteral("tabbed");          // sway's tab titles are not ours to cover
        else if (w.deco.isEmpty() || w.border != QLatin1String("normal")) s.reason = QStringLiteral("no-titlebar");
        else if (in.changedAt.contains(w.id) && in.nowMs - in.changedAt.value(w.id) < in.settleMs) s.reason = QStringLiteral("moving");
        else {
            // A layer surface is above every window: when a window drawn above this one covers the buttons the
            // strip has to give way.
            const QRect mine = s.surface();
            for (int j = i + 1; j < ws.size(); ++j) {
                const FrameWindow &o = ws.at(j);
                if (!o.visible || o.parked || o.workspace != w.workspace || o.output != w.output)
                    continue;
                if (o.fullscreen || o.rect.united(o.deco).intersects(mine)) {
                    s.reason = QStringLiteral("occluded");
                    break;
                }
            }
        }
        s.show = s.reason.isEmpty();
        strips.append(s);
    }
    return strips;
}

QStringList borderCommands(const FrameTree &tree, const FrameConfig &cfg, QSet<int> *framed)
{
    QStringList cmds;
    QSet<int> now;
    for (const FrameWindow &w : tree.windows) {
        if (w.border == QLatin1String("csd")) {
            continue;                 // the window draws its own frame: a second header on top would be wrong
        }
        if (cfg.shouldFrame(w.appId)) {
            now.insert(w.id);
            if (w.border != QLatin1String("normal"))
                cmds << QStringLiteral("[con_id=%1] border normal 1").arg(w.id);
        } else if (framed->contains(w.id) && w.border == QLatin1String("normal")) {
            cmds << QStringLiteral("[con_id=%1] border pixel 1").arg(w.id);
        }
    }
    *framed = now;
    return cmds;
}

TuneStep tuneTitleBar(int observed, int fontSize, int vPad, int target)
{
    TuneStep t;
    t.fontSize = fontSize;
    t.vPad = vPad;
    if (observed == target) {
        t.done = true;
        return t;
    }
    const int fontHeight = observed - 2 * vPad;          // what sway measured for this font
    int want = target - fontHeight;                       // padding sum needed
    if (want >= 2 && want % 2 == 0) {
        t.vPad = want / 2;
        return t;
    }
    // Odd or negative: nudge the font by a pixel so the remainder is even next time.
    t.fontSize = want < 2 ? std::max(8, fontSize - 1) : fontSize + 1;
    if (want >= 2)
        t.vPad = want / 2;      // as close as the padding allows meanwhile
    else
        t.vPad = 1;
    return t;
}
