#include "controller.h"

#include <QAccessible>
#include <QClipboard>
#include <QDateTime>
#include <QGuiApplication>
#include <QJsonDocument>
#include <QJsonObject>

#include "runner.h"

using namespace taos::search;

namespace taos::launcher {

Controller::Controller(SearchEngine *engine, std::shared_ptr<Frecency> frec, QObject *parent)
    : QObject(parent), m_engine(engine), m_frec(std::move(frec)) {
    connect(engine, &SearchEngine::updated, this, &Controller::onUpdated);
    m_launchTimer.setSingleShot(true);
    connect(&m_launchTimer, &QTimer::timeout, this, &Controller::finishLaunch);
    m_noticeTimer.setSingleShot(true);
    m_noticeTimer.setInterval(2600);
    connect(&m_noticeTimer, &QTimer::timeout, this, [this] { setNotice({}); });
}

QStringList Controller::actionLabels() const {
    QStringList l;
    if (const Row *r = m_model.at(m_cur)) for (const Action &a : r->r.actions) l << a.label;
    return l;
}

QString Controller::currentHint() const {
    const Row *r = m_model.at(m_cur);
    if (!r || r->r.actions.isEmpty()) return {};
    if (!m_confirmId.isEmpty() && m_confirmId == r->r.id) return QStringLiteral("Press Enter to confirm");
    return r->r.actions.first().shownHint();
}
QString Controller::currentTitle() const { const Row *r = m_model.at(m_cur); return r ? r->r.title : QString(); }
QString Controller::currentIcon() const { const Row *r = m_model.at(m_cur); return r ? RowModel::iconUrl(r->r.icon) : QString(); }
QString Controller::currentKind() const { const Row *r = m_model.at(m_cur); return r ? r->r.kind : QString(); }

void Controller::setNotice(const QString &n) {
    if (n == m_notice) return;
    m_notice = n;
    emit noticeChanged();
    if (!n.isEmpty()) { m_noticeTimer.start(); announce(n); }
}

void Controller::announce(const QString &text) {
    if (!m_a11y || !QAccessible::isActive() || text.isEmpty()) return;
    QAccessibleAnnouncementEvent ev(m_a11y, text);
    QAccessible::updateAccessibility(&ev);
}

void Controller::show() {
    if (m_open) return;
    m_open = true;
    m_visible = true;
    m_query.clear();
    m_actionsOpen = false;
    m_confirmId.clear();
    m_userMoved = false;
    m_launching = false;
    m_notice.clear();
    m_shownAt = QDateTime::currentMSecsSinceEpoch();
    if (m_cfg) m_cfg->load();
    m_engine->prepare();
    m_model.setRows({});
    m_cur = -1;
    m_gen = m_engine->search(QString());
    emit clearInput();
    emit queryChanged();
    emit actionsChanged();
    emit noticeChanged();
    emit launchingChanged();
    emit showWindow();
    emit openChanged();
}

void Controller::hide() {
    if (!m_open) return;
    m_open = false;
    m_engine->cancel();
    m_launchTimer.stop();
    emit openChanged();   // the QML fades out and calls closeFinished()
}

void Controller::closeFinished() {
    if (m_open) return;   // reopened during the fade
    if (m_visible) { m_visible = false; emit hideWindow(); }
}

void Controller::toggle() { if (m_open) hide(); else show(); }

void Controller::windowDeactivated() {
    // a compositor that hands the keyboard over at show time may report a deactivation first
    if (m_open && QDateTime::currentMSecsSinceEpoch() - m_shownAt > 400) hide();
}
void Controller::windowActivated() {}

QString Controller::handleCommand(const QStringList &w) {
    const QString c = w.value(0);
    if (c == QLatin1String("ping")) return QStringLiteral("ok pong");
    if (c == QLatin1String("toggle")) { toggle(); return QStringLiteral("ok %1").arg(m_open ? "shown" : "hidden"); }
    if (c == QLatin1String("show")) { show(); return QStringLiteral("ok shown"); }
    if (c == QLatin1String("hide")) { hide(); return QStringLiteral("ok hidden"); }
    if (c == QLatin1String("status")) {
        return QStringLiteral("ok ") + QString::fromUtf8(QJsonDocument(QJsonObject{{QStringLiteral("open"), m_open}, {QStringLiteral("query"), m_query},
                                                                                 {QStringLiteral("rows"), m_model.count()}}).toJson(QJsonDocument::Compact));
    }
    return QStringLiteral("error: unknown command %1").arg(c);
}

void Controller::setQuery(const QString &text) {
    if (text == m_query) return;
    m_query = text;
    m_confirmId.clear();
    m_userMoved = false;
    m_searching = true;
    if (m_actionsOpen) { m_actionsOpen = false; emit actionsChanged(); }
    emit queryChanged();
    m_gen = m_engine->search(text);
}

void Controller::onUpdated(int gen, const QList<Row> &rows, bool final) {
    if (gen != m_gen || !m_open) return;
    QString keep;
    if (m_userMoved) if (const Row *r = m_model.at(m_cur)) keep = r->r.id;
    m_model.setRows(rows);
    m_searching = !final;
    int idx = rows.isEmpty() ? -1 : 0;
    if (!keep.isEmpty()) { const int k = m_model.indexOfId(keep); if (k >= 0) idx = k; }
    if (m_actionsOpen && (idx < 0 || m_model.at(idx)->r.id != keep)) { m_actionsOpen = false; emit actionsChanged(); }
    m_cur = idx;
    emit rowsChanged();
    emit currentChanged();
    if (final) {
        const int n = rows.size();
        announce(n == 0 ? QStringLiteral("No results") : QStringLiteral("%1 results").arg(n));
    }
}

void Controller::select(int i, bool byUser) {
    if (i < 0 || i >= m_model.count() || i == m_cur) return;
    m_cur = i;
    m_confirmId.clear();
    if (byUser) m_userMoved = true;
    emit currentChanged();
    if (byUser) announce(m_model.data(m_model.index(i), RowModel::AccessibleRole).toString());
}

void Controller::move(int delta) {
    if (m_actionsOpen) { moveAction(delta); return; }
    const int n = m_model.count();
    if (n == 0) return;
    int i = m_cur + delta;
    if (i < 0) i = n - 1;
    if (i >= n) i = 0;
    select(i, true);
}

void Controller::hoverIndex(int i) { if (!m_actionsOpen) select(i, true); }

void Controller::openActions() {
    const Row *r = m_model.at(m_cur);
    if (!r || r->r.actions.size() < 1) return;
    if (r->r.kind == QLatin1String("mention") && r->r.actions.first().verb == QLatin1String("complete")) { perform(r->r, 0); return; }   // Tab completes a mention
    m_actionsOpen = true;
    m_actionIdx = 0;
    emit actionsChanged();
    announce(QStringLiteral("Actions for %1").arg(r->r.title));
}
void Controller::closeActions() { if (m_actionsOpen) { m_actionsOpen = false; emit actionsChanged(); } }
void Controller::moveAction(int delta) {
    const int n = actionLabels().size();
    if (n == 0) return;
    m_actionIdx = (m_actionIdx + delta + n) % n;
    emit actionsChanged();
}
void Controller::setActionIndex(int i) {
    if (i == m_actionIdx || i < 0 || i >= actionLabels().size()) return;
    m_actionIdx = i;
    emit actionsChanged();
}
void Controller::runActionAt(int i) {
    const Row *r = m_model.at(m_cur);
    if (!r) return;
    perform(r->r, i);
}

void Controller::activate() {
    const Row *r = m_model.at(m_cur);
    if (!r) return;
    perform(r->r, m_actionsOpen ? m_actionIdx : 0);
}

void Controller::activateAlternate() {
    const Row *r = m_model.at(m_cur);
    if (r && r->r.kind == QLatin1String("mention") && r->r.actions.size() >= 2 && r->r.actions.first().verb == QLatin1String("send-mention")) {
        perform(r->r, 1);
        return;
    }
    activate();
}

void Controller::activateIndex(int i) {
    if (i < 0 || i >= m_model.count()) return;
    m_cur = i;
    emit currentChanged();
    perform(m_model.at(i)->r, 0);
}

void Controller::quickPick(int n) {
    if (m_actionsOpen || n < 1 || n > m_model.count()) return;
    select(n - 1, true);
    perform(m_model.at(n - 1)->r, 0);
}

bool Controller::perform(const Result &r, int ai) {
    if (ai < 0 || ai >= r.actions.size()) return false;
    const Action &a = r.actions[ai];
    if (a.confirm && m_confirmId != r.id) {
        m_confirmId = r.id;
        emit currentChanged();
        announce(QStringLiteral("Press Enter again to confirm %1").arg(r.title));
        return false;
    }
    m_confirmId.clear();
    if (a.verb == QLatin1String("complete")) { emit setInputText(a.data); return false; }
    if (a.verb == QLatin1String("send-mention")) {
        if (!m_sender || a.args.isEmpty()) { setNotice(QStringLiteral("Chat is not connected yet")); return false; }
        const bool openChat = a.id == QLatin1String("send-open");
        const QString names = r.title.startsWith(QLatin1String("Send to ")) ? r.title.mid(8) : r.title;
        const QString first = a.args.first();
        setNotice(QStringLiteral("Sending to %1").arg(names));
        std::shared_ptr<taos::search::ChatSender> sender = m_sender;
        sender->send(a.args, a.data, [this, names, openChat, first, sender](bool ok, const QString &msg) {
            if (!ok) { setNotice(msg.isEmpty() ? QStringLiteral("Could not send") : msg); return; }
            QString text = QStringLiteral("Sent to %1").arg(names);
            if (openChat && !sender->openChat(first)) text += QStringLiteral(". Chat window is not available yet: the reply will arrive as a notification");
            setNotice(text);
            m_launching = true;
            emit launchingChanged();
            m_launchTimer.start(openChat ? 1800 : 1100);
        });
        return true;
    }
    QString err;
    bool ok;
    if (a.verb == QLatin1String("ask-open")) {
        QGuiApplication::clipboard()->setText(a.data);
        Action l = a;
        l.verb = QStringLiteral("launch");
        l.data.clear();
        ok = runAction(l, &err);
    } else if (a.verb == QLatin1String("copy")) {
        QGuiApplication::clipboard()->setText(a.data);
        ok = true;
    } else {
        ok = runAction(a, &err);
    }
    if (!ok) {
        setNotice(err.isEmpty() ? QStringLiteral("That did not work") : err);
        return false;
    }
    m_frec->record(r.id);
    if (a.verb == QLatin1String("copy")) announce(QStringLiteral("Copied %1").arg(a.data));
    m_launching = true;
    emit launchingChanged();
    m_launchTimer.start(m_launchMs);
    return true;
}

void Controller::finishLaunch() { hide(); }

void Controller::escape() {
    if (m_actionsOpen) { closeActions(); return; }
    if (!m_confirmId.isEmpty()) { m_confirmId.clear(); emit currentChanged(); return; }
    if (!m_query.isEmpty()) { emit clearInput(); setQuery(QString()); return; }
    hide();
}

}  // namespace taos::launcher
