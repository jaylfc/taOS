#include "controller.h"

#include <QAccessible>
#include <QCoreApplication>
#include <QDateTime>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSet>
#include <algorithm>
#include <cmath>

#include "iconprovider.h"
#include "packing.h"

Controller::Controller(AppInfo *apps, QObject *parent) : QObject(parent), m_apps(apps), m_model(apps) {
    m_model.setIconResolver([this](const QString &id) { return iconFor(id); });
    m_refreshTimer.setSingleShot(true);
    m_refreshTimer.setInterval(20);
    connect(&m_refreshTimer, &QTimer::timeout, this, &Controller::refresh);
    // The overlay owns the keyboard while it is up, so it never stays up on its own: after this much time
    // without a key, click or command it closes itself.
    m_idle.setSingleShot(true);
    bool ok = false;
    const int ms = qEnvironmentVariableIntValue("TAOS_OVERVIEW_IDLE_MS", &ok);
    m_idle.setInterval(ok && ms > 0 ? ms : 30000);
    connect(&m_idle, &QTimer::timeout, this, [this] {
        if (m_mode == QLatin1String("hidden")) return;
        fprintf(stderr, "taos-overview: no input for %d ms, closing\n", m_idle.interval());
        cancel();
    });
    connect(&m_ev, &SwayIpc::event, this, &Controller::onEvent);
    connect(&m_ev, &SwayIpc::disconnected, this, [] { /* sway is gone: the session ends with it */ QCoreApplication::quit(); });
}

void Controller::setCapture(CaptureWorker *c) {
    m_cap = c;
    connect(c, &CaptureWorker::frame, this, [this](const QString &id, const QImage &img) {
        m_cap->frameConsumed();
        if (m_store) m_store->put(id, img);
    });
}

QString Controller::iconFor(const QString &appId) {
    auto it = m_iconCache.find(appId);
    if (it != m_iconCache.end()) return it.value();
    const QString r = IconProvider::resolve(m_apps, appId).isNull() ? QString()
                                                                    : QStringLiteral("image://icon/") + appId;
    m_iconCache.insert(appId, r);
    return r;
}

bool Controller::connectSway(QString *error) {
    if (!m_cmd.connectToSway() || !m_ev.connectToSway()) {
        if (error) *error = QStringLiteral("cannot connect to the sway socket (SWAYSOCK=%1)").arg(SwayIpc::socketPath());
        return false;
    }
    m_ev.subscribe({ QStringLiteral("window"), QStringLiteral("workspace") });
    refresh();
    return true;
}

void Controller::scheduleRefresh() {
    if (!m_refreshTimer.isActive()) m_refreshTimer.start();
}

void Controller::refresh() {
    m_cmd.request(SwayWire::GetTree, {}, [this](bool ok, const QByteArray &p) {
        if (!ok) return;
        Snapshot s;
        QString err;
        if (!SwayTree::parse(p, &s, &err)) {
            fprintf(stderr, "taos-overview: get_tree: %s\n", qPrintable(err));
            return;
        }
        applySnapshot(s);
    });
}

void Controller::applySnapshot(const Snapshot &s) {
    QVector<int> ids;
    for (const WinInfo &w : s.windows) ids << w.conId;
    const bool first = !m_haveSnapshot;
    m_snap = s;
    m_haveSnapshot = true;
    if (first) m_mru.reconcile(ids, s.seedOrder);
    else m_mru.reconcile(ids, {});
    if (m_mode == QLatin1String("hidden") && s.focusedConId && !m_mru.order().isEmpty() && m_mru.order().first() != s.focusedConId)
        m_mru.focus(s.focusedConId);
    if (m_mode == QLatin1String("hidden")) {
        m_output = s.focusedOutput.isEmpty() && !s.outputs.isEmpty() ? s.outputs.first().name : s.focusedOutput;
        emit outputNameChanged();
    }
    for (const OutInfo &o : s.outputs) if (o.name == m_output) m_curWs = o.currentWorkspace;
    QHash<QString, QRect> wr;
    for (const WsInfo &w : s.workspaces) wr.insert(w.output + QLatin1Char('\n') + w.name, w.rect);
    m_model.setWorkspaceRects(wr);
    if (m_store) {
        QSet<QString> keep;
        for (const WinInfo &w : s.windows) keep.insert(w.identifier);
        m_store->retainOnly(keep);
    }
    if (m_mode != QLatin1String("hidden")) {
        const int selConId = (m_selected >= 0 && m_selected < m_model.rowCount()) ? m_model.at(m_selected).conId : 0;
        const bool changed = m_model.setWindows(m_snap.windows, true);
        if (m_model.rowCount() == 0 && m_mode == QLatin1String("switcher")) { cancel(); return; }
        if (changed) {
            const int r = selConId ? m_model.rowOf(selConId) : -1;
            m_selected = r >= 0 ? r : std::min(m_selected, int(m_model.rowCount()) - 1);
            emit selectedIndexChanged();
        }
        recomputeLayout();
        rebuildWorkspaces();
        updateCapture();
    }
    emit countChanged();
    emit workspacesChanged();
}

static QJsonObject objOf(const QByteArray &b) { return QJsonDocument::fromJson(b).object(); }

void Controller::onEvent(quint32 type, const QByteArray &payload) {
    if (type == SwayWire::EventWorkspace) { scheduleRefresh(); return; }
    if (type != SwayWire::EventWindow) return;
    const QJsonObject o = objOf(payload);
    const QString change = o.value(QLatin1String("change")).toString();
    const QJsonObject c = o.value(QLatin1String("container")).toObject();
    const int id = c.value(QLatin1String("id")).toInt();
    int idx = -1;
    for (int i = 0; i < m_snap.windows.size(); ++i) if (m_snap.windows[i].conId == id) idx = i;
    const bool visible = m_mode != QLatin1String("hidden");
    if (change == QLatin1String("focus") && idx >= 0) {
        m_mru.focus(id);
        for (WinInfo &w : m_snap.windows) w.focused = (w.conId == id);
        m_snap.focusedConId = id;
        m_snap.focusedWorkspace = m_snap.windows[idx].workspace;
        m_snap.focusedOutput = m_snap.windows[idx].output;
        for (OutInfo &out : m_snap.outputs) {
            if (out.name == m_snap.focusedOutput) out.currentWorkspace = m_snap.focusedWorkspace;
            out.focused = out.name == m_snap.focusedOutput;
        }
        if (!visible) { m_output = m_snap.focusedOutput; m_curWs = m_snap.focusedWorkspace; }
        if (visible) m_model.setWindows(m_snap.windows, true);
    } else if ((change == QLatin1String("title") || change == QLatin1String("urgent")) && idx >= 0) {
        WinInfo n;
        if (SwayTree::parseWindowNode(QJsonDocument(c).toJson(QJsonDocument::Compact), &n)) {
            m_snap.windows[idx].title = n.title;
            m_snap.windows[idx].urgent = n.urgent;
            if (visible) m_model.setWindows(m_snap.windows, true);
        }
    } else if (change == QLatin1String("close") && idx >= 0) {
        m_snap.windows.removeAt(idx);
        m_mru.remove(id);
        if (visible) applySnapshot(m_snap);
        else emit countChanged();
    } else if (change != QLatin1String("mark")) {
        scheduleRefresh();
    }
}

// ---- commands ---------------------------------------------------------------------------------

QString Controller::handleCommand(const QStringList &w) {
    if (w.isEmpty()) return QStringLiteral("error: empty command");
    const QString v = w[0];
    touch();   // a command is activity (a held Tab arrives as a stream of them)
    const bool reverse = w.contains(QStringLiteral("--reverse"));
    const bool sticky = w.contains(QStringLiteral("--sticky"));
    if (v == QLatin1String("ping")) return QStringLiteral("ok");
    if (v == QLatin1String("switcher")) {
        if (m_mode == QLatin1String("hidden")) open(QStringLiteral("switcher"), reverse, sticky);
        else if (m_mode == QLatin1String("switcher")) move(reverse ? QStringLiteral("prev") : QStringLiteral("next"));
        else { setMode(QStringLiteral("switcher")); move(reverse ? QStringLiteral("prev") : QStringLiteral("next")); }
        return QStringLiteral("ok");
    }
    if (v == QLatin1String("overview")) {
        if (m_mode == QLatin1String("hidden")) open(QStringLiteral("overview"), false, true);
        else if (m_mode == QLatin1String("overview")) cancel();
        else { setMode(QStringLiteral("overview")); m_sticky = true; recomputeLayout(); rebuildWorkspaces(); updateCapture(); }
        return QStringLiteral("ok");
    }
    if (v == QLatin1String("hide")) { cancel(); return QStringLiteral("ok"); }
    if (v == QLatin1String("commit")) { commit(); return QStringLiteral("ok"); }
    if (v == QLatin1String("next") || v == QLatin1String("prev") || v == QLatin1String("left") || v == QLatin1String("right")
        || v == QLatin1String("up") || v == QLatin1String("down")) { move(v); return QStringLiteral("ok"); }
    if (v == QLatin1String("close-selected")) { closeSelected(); return QStringLiteral("ok"); }
    if (v == QLatin1String("type") && w.size() > 1) { typeText(w.mid(1).join(QLatin1Char(' '))); return QStringLiteral("ok"); }
    if (v == QLatin1String("status")) return QStringLiteral("ok ") + statusJson();
    return QStringLiteral("error: unknown command '%1' (switcher, overview, hide, commit, next, prev, status)").arg(v);
}

void Controller::setMode(const QString &m) {
    if (m == m_mode) return;
    m_mode = m;
    emit modeChanged();
}

void Controller::open(const QString &mode, bool reverse, bool sticky) {
    if (!m_haveSnapshot) return;
    if (mode == QLatin1String("switcher") && m_snap.windows.isEmpty()) return;   // nothing to show
    m_sticky = sticky;
    m_filter.clear();
    emit filterTextChanged();
    m_model.setWindows(m_mru.sorted(m_snap.windows), false);
    const int n = m_model.rowCount();
    if (mode == QLatin1String("switcher")) m_selected = n > 1 ? (reverse ? n - 1 : 1) : 0;
    else m_selected = -1;
    if (m_output.isEmpty() && !m_snap.outputs.isEmpty()) m_output = m_snap.outputs.first().name;
    for (const OutInfo &o : m_snap.outputs) if (o.name == m_output) m_curWs = o.currentWorkspace;
    emit selectedIndexChanged();
    m_windowVisible = true;
    m_showTimer.start();
    setMode(mode);
    m_idle.start();
    recomputeLayout();
    rebuildWorkspaces();
    updateCapture();
    emit windowVisibleChanged();
    emit showWindow(m_output);
    emit countChanged();
    emit workspacesChanged();
    selectionAnnounce();
}

void Controller::touch() {
    if (m_mode != QLatin1String("hidden")) m_idle.start();
}

void Controller::startClose() {
    if (m_mode == QLatin1String("hidden")) return;
    m_idle.stop();
    setMode(QStringLiteral("hidden"));
    emit releaseKeyboard();
    updateCapture();
    QTimer::singleShot(700, this, [this] { if (m_mode == QLatin1String("hidden") && m_windowVisible) closeFinished(); });
}

void Controller::closeFinished() {
    if (m_mode != QLatin1String("hidden") || !m_windowVisible) return;
    m_windowVisible = false;
    emit windowVisibleChanged();
    emit hideWindow();
}

void Controller::cancel() { startClose(); }

void Controller::noteFirstFrame() {
    if (m_framed) return;
    m_lastShowMs = m_showTimer.isValid() ? m_showTimer.elapsed() : -1;
    m_framed = true;
    emit framedChanged();
}

void Controller::commit() {
    if (m_mode == QLatin1String("hidden")) return;
    int id = 0;
    if (m_selected >= 0 && m_selected < m_model.rowCount()) id = m_model.at(m_selected).conId;
    startClose();
    if (id) m_cmd.command(QStringLiteral("[con_id=%1] focus").arg(id));
}

void Controller::focusWindow(int conId) {
    startClose();
    m_cmd.command(QStringLiteral("[con_id=%1] focus").arg(conId));
}

void Controller::closeWindow(int conId) { m_cmd.command(QStringLiteral("[con_id=%1] kill").arg(conId)); }

void Controller::closeSelected() {
    if (m_selected >= 0 && m_selected < m_model.rowCount()) closeWindow(m_model.at(m_selected).conId);
}

void Controller::goToWorkspace(const QString &name, int num) {
    startClose();
    if (num >= 0) m_cmd.command(QStringLiteral("workspace number %1").arg(num));
    else m_cmd.command(QStringLiteral("workspace \"%1\"").arg(QString(name).replace(QLatin1Char('"'), QStringLiteral("\\\""))));
}

void Controller::windowActivated(bool altHeld) {
    if (m_mode == QLatin1String("switcher") && !m_sticky && !altHeld) commit();
}

void Controller::setSelectedIndex(int i) {
    if (i == m_selected) return;
    m_selected = i;
    emit selectedIndexChanged();
    selectionAnnounce();
}

void Controller::selectionAnnounce() {
    if (m_selected >= 0 && m_selected < m_model.rowCount())
        announce(m_model.accessibleText(m_model.at(m_selected)));
}

void Controller::announce(const QString &text) {
    if (!accessibleTarget || !QAccessible::isActive()) return;
    QAccessibleAnnouncementEvent ev(accessibleTarget, text);
    QAccessible::updateAccessibility(&ev);
}

bool Controller::matches(const WinInfo &w) const {
    return WindowFilter::matches(w, m_apps ? m_apps->displayName(w.appId) : w.appId, m_filter);
}

void Controller::typeText(const QString &t) {
    if (m_mode != QLatin1String("overview")) return;
    m_filter += t;
    emit filterTextChanged();
    recomputeLayout();
    updateCapture();
    if (!m_layoutIds.isEmpty()) setSelectedIndex(m_model.rowOf(m_layoutIds.first()));
    else setSelectedIndex(-1);
}

void Controller::backspace() {
    if (m_filter.isEmpty()) return;
    m_filter.chop(1);
    emit filterTextChanged();
    recomputeLayout();
    updateCapture();
    setSelectedIndex(m_layoutIds.isEmpty() ? -1 : m_model.rowOf(m_layoutIds.first()));
}

void Controller::clearFilter() {
    if (m_filter.isEmpty()) return;
    m_filter.clear();
    emit filterTextChanged();
    recomputeLayout();
    updateCapture();
}

int Controller::overviewNeighbour(const QString &dir) const {
    if (m_layoutIds.isEmpty()) return -1;
    const int curId = (m_selected >= 0 && m_selected < m_model.rowCount()) ? m_model.at(m_selected).conId : 0;
    if (!curId || !m_layoutMap.contains(QString::number(curId))) {
        // nothing selected yet: start from the focused window, else the first
        for (int id : m_layoutIds) if (m_model.at(m_model.rowOf(id)).focused) return m_model.rowOf(id);
        return m_model.rowOf(m_layoutIds.first());
    }
    const QRectF cur = m_layoutMap.value(QString::number(curId)).toRectF();
    const QPointF c = cur.center();
    int best = -1;
    qreal bestScore = 1e18;
    for (int id : m_layoutIds) {
        if (id == curId) continue;
        const QPointF p = m_layoutMap.value(QString::number(id)).toRectF().center();
        const qreal dx = p.x() - c.x(), dy = p.y() - c.y();
        qreal along, across;
        if (dir == QLatin1String("left")) { along = -dx; across = std::abs(dy); }
        else if (dir == QLatin1String("right")) { along = dx; across = std::abs(dy); }
        else if (dir == QLatin1String("up")) { along = -dy; across = std::abs(dx); }
        else { along = dy; across = std::abs(dx); }
        if (along <= 1) continue;
        const qreal score = along + across * 2.2;
        if (score < bestScore) { bestScore = score; best = id; }
    }
    return best < 0 ? m_selected : m_model.rowOf(best);
}

void Controller::move(const QString &dir) {
    const int n = m_model.rowCount();
    if (n == 0) return;
    if (m_mode == QLatin1String("switcher")) {
        int i = m_selected < 0 ? 0 : m_selected;
        if (dir == QLatin1String("next")) i = (i + 1) % n;
        else if (dir == QLatin1String("prev")) i = (i + n - 1) % n;
        else if (dir == QLatin1String("right") || dir == QLatin1String("down")) i = std::min(n - 1, i + 1);
        else if (dir == QLatin1String("left") || dir == QLatin1String("up")) i = std::max(0, i - 1);
        setSelectedIndex(i);
    } else if (m_mode == QLatin1String("overview")) {
        if (m_layoutIds.isEmpty()) return;
        int r;
        if (dir == QLatin1String("next") || dir == QLatin1String("prev")) {
            const int curId = (m_selected >= 0 && m_selected < n) ? m_model.at(m_selected).conId : 0;
            int pos = m_layoutIds.indexOf(curId);
            const int k = m_layoutIds.size();
            pos = pos < 0 ? (dir == QLatin1String("next") ? 0 : k - 1) : (dir == QLatin1String("next") ? (pos + 1) % k : (pos + k - 1) % k);
            r = m_model.rowOf(m_layoutIds[pos]);
        } else {
            r = overviewNeighbour(dir);
        }
        if (r >= 0) setSelectedIndex(r);
    }
}

// ---- layout, workspaces, capture ---------------------------------------------------------------

void Controller::setOverviewArea(const QRectF &area) {
    if (area == m_area) return;
    m_area = area;
    if (m_mode == QLatin1String("overview")) { recomputeLayout(); updateCapture(); }
}
void Controller::setCardSize(const QSize &s) {
    if (s == m_card) return;
    m_card = s;
    updateCapture();
}
void Controller::setMiniSize(const QSize &s) {
    if (s == m_mini) return;
    m_mini = s;
    updateCapture();
}

void Controller::recomputeLayout() {
    QVariantMap map;
    QVector<int> ids;
    if (m_mode == QLatin1String("overview") && m_area.width() > 10 && m_area.height() > 10) {
        QHash<QString, QRect> wr;
        for (const WsInfo &w : m_snap.workspaces) wr.insert(w.output + QLatin1Char('\n') + w.name, w.rect);
        QVector<Packing::Item> items;
        for (const WinInfo &w : m_model.all()) {
            const bool include = m_filter.isEmpty() ? (w.workspace == m_curWs && w.output == m_output) : matches(w);
            if (!include) continue;
            const QRect ws = wr.value(w.output + QLatin1Char('\n') + w.workspace);
            items.push_back({ w.conId, QRectF(w.rect).translated(-ws.topLeft()) });
        }
        Packing::Options o;
        o.gap = std::max<qreal>(24, m_area.width() / 70);
        o.minDim = std::min<qreal>(260, m_area.height() / 3);
        o.maxScale = 1.0;
        const QVector<QRectF> rects = Packing::pack(items, m_area, o);
        QVector<QPair<QRectF, int>> order;
        for (int i = 0; i < items.size(); ++i) {
            map.insert(QString::number(items[i].id), rects[i]);
            order.push_back({ rects[i], items[i].id });
        }
        std::sort(order.begin(), order.end(), [](const auto &a, const auto &b) {
            if (std::abs(a.first.center().y() - b.first.center().y()) > 8) return a.first.center().y() < b.first.center().y();
            return a.first.center().x() < b.first.center().x();
        });
        for (const auto &p : order) ids << p.second;
    }
    if (map != m_layoutMap || ids != m_layoutIds) {
        m_layoutMap = map;
        m_layoutIds = ids;
        emit layoutChanged();
    }
}

void Controller::rebuildWorkspaces() {
    QVariantList list;
    QVector<WsInfo> wss = m_snap.workspaces;
    std::stable_sort(wss.begin(), wss.end(), [](const WsInfo &a, const WsInfo &b) {
        const int na = a.num < 0 ? 1 << 20 : a.num, nb = b.num < 0 ? 1 << 20 : b.num;
        return na < nb;
    });
    int maxNum = 0;
    for (const WsInfo &ws : wss) {
        maxNum = std::max(maxNum, ws.num);
        QVariantList wins;
        // windows in the workspace's stacking order: tiled first, floating last, so floating ones paint on top
        QVector<WinInfo> inWs;
        for (const WinInfo &w : m_snap.windows) if (w.workspace == ws.name && w.output == ws.output) inWs.push_back(w);
        std::stable_sort(inWs.begin(), inWs.end(), [](const WinInfo &a, const WinInfo &b) { return !a.floating && b.floating; });
        for (const WinInfo &w : inWs) {
            wins << QVariantMap { { QStringLiteral("conId"), w.conId }, { QStringLiteral("identifier"), w.identifier },
                                  { QStringLiteral("rect"), QRectF(w.rect).translated(-ws.rect.topLeft()) },
                                  { QStringLiteral("focused"), w.focused } };
        }
        list << QVariantMap { { QStringLiteral("name"), ws.name }, { QStringLiteral("num"), ws.num },
                              { QStringLiteral("output"), ws.output },
                              { QStringLiteral("current"), ws.name == m_curWs && ws.output == m_output },
                              { QStringLiteral("size"), QSizeF(ws.rect.size()) }, { QStringLiteral("windows"), wins },
                              { QStringLiteral("isNew"), false }, { QStringLiteral("urgent"), ws.urgent },
                              { QStringLiteral("count"), int(wins.size()) } };
    }
    QSizeF ref(1920, 1080);
    for (const OutInfo &o : m_snap.outputs) if (o.name == m_output) ref = QSizeF(o.rect.size());
    list << QVariantMap { { QStringLiteral("name"), QString::number(maxNum + 1) }, { QStringLiteral("num"), maxNum + 1 },
                          { QStringLiteral("output"), m_output }, { QStringLiteral("current"), false },
                          { QStringLiteral("size"), ref }, { QStringLiteral("windows"), QVariantList() },
                          { QStringLiteral("isNew"), true }, { QStringLiteral("urgent"), false }, { QStringLiteral("count"), 0 } };
    m_wsList = list;
}

void Controller::updateCapture() {
    if (!m_cap || !m_cap->available()) return;
    QHash<QString, QSize> t;
    if (m_mode != QLatin1String("hidden")) {
        const auto px = [&](qreal w, qreal h) { return QSize(int(std::ceil(w * m_dpr)), int(std::ceil(h * m_dpr))); };
        for (const WinInfo &w : m_model.all()) {
            if (w.identifier.isEmpty()) continue;
            if (m_mode == QLatin1String("switcher")) {
                t.insert(w.identifier, px(m_card.width(), m_card.height()));
            } else {
                const auto it = m_layoutMap.constFind(QString::number(w.conId));
                if (it != m_layoutMap.constEnd()) {
                    const QRectF r = it.value().toRectF();
                    t.insert(w.identifier, px(r.width(), r.height()));
                } else {
                    t.insert(w.identifier, px(m_mini.width(), m_mini.height()));
                }
            }
        }
    }
    m_cap->setTargets(t);
}

QString Controller::statusJson() {
    QJsonObject o;
    o.insert(QStringLiteral("mode"), m_mode);
    o.insert(QStringLiteral("windows"), int(m_model.rowCount()));
    o.insert(QStringLiteral("selected"), m_selected);
    o.insert(QStringLiteral("output"), m_output);
    o.insert(QStringLiteral("filter"), m_filter);
    o.insert(QStringLiteral("layout"), int(m_layoutIds.size()));
    o.insert(QStringLiteral("workspace"), m_curWs);
    o.insert(QStringLiteral("last_show_ms"), double(m_lastShowMs));
    o.insert(QStringLiteral("capture"), m_cap && m_cap->available());
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    QJsonObject fps;
    if (m_cap) {
        const double secs = m_lastStatusMs ? (now - m_lastStatusMs) / 1000.0 : 0;
        const QHash<QString, int> c = m_cap->takeFrameCounts();
        for (auto it = c.begin(); it != c.end(); ++it) fps.insert(it.key(), secs > 0 ? it.value() / secs : 0.0);
    }
    m_lastStatusMs = now;
    o.insert(QStringLiteral("fps"), fps);
    QJsonArray sel;
    for (const WinInfo &w : m_model.all()) {
        QJsonObject wo;
        wo.insert(QStringLiteral("con_id"), w.conId);
        wo.insert(QStringLiteral("title"), w.title);
        wo.insert(QStringLiteral("workspace"), w.workspace);
        sel.append(wo);
    }
    o.insert(QStringLiteral("list"), sel);
    QFile f(QStringLiteral("/proc/self/status"));
    if (f.open(QIODevice::ReadOnly))
        for (const QByteArray &l : f.readAll().split('\n'))
            if (l.startsWith("VmRSS:")) o.insert(QStringLiteral("rss_kb"), l.mid(6).trimmed().split(' ').first().toInt());
    return QString::fromUtf8(QJsonDocument(o).toJson(QJsonDocument::Compact));
}
