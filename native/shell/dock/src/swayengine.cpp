#include "swayengine.h"

#include <QJsonDocument>
#include <cstdio>

SwayEngine::SwayEngine(const QString &socketPath, QObject *parent) : WindowEngine(parent), m_path(socketPath) {
    m_debounce.setSingleShot(true);
    m_debounce.setInterval(20);
    connect(&m_debounce, &QTimer::timeout, this, &SwayEngine::refresh);
    m_reconnect.setSingleShot(true);
    m_reconnect.setInterval(1000);
    connect(&m_reconnect, &QTimer::timeout, this, &SwayEngine::start);
    connect(&m_ev, &SwayIpc::event, this, [this](quint32 type, const QByteArray &) {
        if (type == SwayWire::EventWindow || type == SwayWire::EventWorkspace) scheduleRefresh();
        if (type == SwayWire::EventWorkspace) refreshWorkspace();
    });
    connect(&m_ev, &SwayIpc::disconnected, this, [this] { m_reconnect.start(); });
}

void SwayEngine::start() {
    if (m_req.isConnected() && m_ev.isConnected()) return;
    if (!m_req.isConnected() && !m_req.connectToSway(m_path)) { m_reconnect.start(); return; }
    if (!m_ev.isConnected() && !m_ev.connectToSway(m_path)) { m_reconnect.start(); return; }
    m_ev.subscribe({QStringLiteral("window"), QStringLiteral("workspace")});
    refresh();
    refreshWorkspace();
}

void SwayEngine::scheduleRefresh() { m_debounce.start(); }

void SwayEngine::refresh() {
    m_req.request(SwayWire::GetTree, QByteArray(), [this](bool ok, const QByteArray &payload) {
        if (!ok) return;
        QVector<WindowInfo> w;
        QString err;
        if (!WindowJson::parseSwayTree(payload, &w, &err)) {
            fprintf(stderr, "taos-dock: sway tree: %s\n", qPrintable(err));
            return;
        }
        if (m_have && w == m_windows) return;
        m_have = true;
        m_windows = w;
        emit windowsChanged();
    });
}

const WindowInfo *SwayEngine::find(int id) const {
    for (const WindowInfo &w : m_windows)
        if (w.id == id) return &w;
    return nullptr;
}

void SwayEngine::run(const QString &cmd) {
    m_req.command(cmd, [this](bool, const QByteArray &) { scheduleRefresh(); });
}

QString SwayEngine::minimiseCommand(int id, bool floating) {
    QString c = QStringLiteral("[con_id=%1] mark --add %2").arg(id).arg(WindowJson::minMark(id));
    if (floating) c += QStringLiteral("; [con_id=%1] mark --add taos_float_%1").arg(id);
    c += QStringLiteral("; [con_id=%1] move scratchpad").arg(id);
    return c;
}

QString SwayEngine::restoreCommand(int id, bool wasFloating) {
    QString c = QStringLiteral("[con_id=%1] scratchpad show").arg(id);
    if (!wasFloating) c += QStringLiteral("; [con_id=%1] floating disable").arg(id);
    c += QStringLiteral("; [con_id=%1] unmark %2; [con_id=%1] unmark taos_float_%1; [con_id=%1] focus").arg(id).arg(WindowJson::minMark(id));
    return c;
}

void SwayEngine::close(int id) { run(QStringLiteral("[con_id=%1] kill").arg(id)); }

void SwayEngine::minimise(int id) {
    const WindowInfo *w = find(id);
    if (w && w->minimised) return;
    run(minimiseCommand(id, w && w->floating));
}

void SwayEngine::restore(int id) {
    const WindowInfo *w = find(id);
    if (w && !w->minimised) { focus(id); return; }
    // taos_float_<id> is only set when the window was floating before it was minimised; ask the
    // compositor (the mark) rather than remembering: the tree has the marks.
    m_req.request(SwayWire::GetTree, QByteArray(), [this, id](bool ok, const QByteArray &payload) {
        bool floating = false;
        if (ok) floating = payload.contains(QByteArray("taos_float_") + QByteArray::number(id).append('"'));
        run(restoreCommand(id, floating));
    });
}

void SwayEngine::toggleMinimise(int id) {
    const WindowInfo *w = find(id);
    if (w && w->minimised) restore(id);
    else minimise(id);
}

void SwayEngine::focus(int id) {
    const WindowInfo *w = find(id);
    if (w && w->minimised) { restore(id); return; }
    run(QStringLiteral("[con_id=%1] focus").arg(id));
}

void SwayEngine::full(int id) { run(QStringLiteral("[con_id=%1] fullscreen toggle").arg(id)); }

void SwayEngine::refreshWorkspace() {
    m_req.request(SwayWire::GetWorkspaces, QByteArray(), [this](bool ok, const QByteArray &payload) {
        if (!ok) return;
        QString ws;
        if (!WindowJson::focusedWorkspace(payload, &ws)) return;
        if (ws == m_focusedWs) return;
        m_focusedWs = ws;
        emit focusedWorkspaceChanged();
    });
}
