// SPDX-License-Identifier: AGPL-3.0-or-later
#include "engine.h"

#include <QMetaObject>
#include <QPointer>
#include <algorithm>

namespace taos::search {

double kindWeight(const QString &k) {
    if (k == QLatin1String("calc")) return 1.4;
    if (k == QLatin1String("app")) return 1.0;
    if (k == QLatin1String("window")) return 0.97;
    if (k == QLatin1String("command")) return 0.92;
    if (k == QLatin1String("setting")) return 0.85;
    if (k == QLatin1String("file")) return 0.8;
    if (k == QLatin1String("mention")) return 2.0;
    return 0.5;
}

static int sectionRank(const QString &kind) {
    static const QStringList order = { QStringLiteral("calc"), QStringLiteral("mention"), QStringLiteral("app"), QStringLiteral("window"),
                                       QStringLiteral("command"), QStringLiteral("file"), QStringLiteral("setting"), QStringLiteral("ask") };
    const int i = order.indexOf(kind);
    return i < 0 ? 50 : i;
}

static QString sectionTitle(const QString &kind, bool suggestions) {
    if (kind == QLatin1String("app")) return suggestions ? QStringLiteral("Frequent") : QStringLiteral("Apps");
    if (kind == QLatin1String("window")) return QStringLiteral("Windows");
    if (kind == QLatin1String("command")) return QStringLiteral("Actions");
    if (kind == QLatin1String("file")) return QStringLiteral("Files");
    if (kind == QLatin1String("setting")) return QStringLiteral("Settings");
    if (kind == QLatin1String("calc")) return QStringLiteral("Calculator");
    if (kind == QLatin1String("mention")) return QStringLiteral("Agents");
    if (kind == QLatin1String("ask")) return QStringLiteral("Ask");
    return QStringLiteral("More");
}

static int capFor(const QString &kind) {
    if (kind == QLatin1String("app")) return 5;
    if (kind == QLatin1String("window")) return 4;
    if (kind == QLatin1String("command")) return 4;
    if (kind == QLatin1String("file")) return 5;
    if (kind == QLatin1String("setting")) return 3;
    if (kind == QLatin1String("mention")) return 8;
    return 6;
}

QList<Row> arrange(const QList<Result> &all, const QString &text) {
    const bool suggestions = text.trimmed().isEmpty();
    QList<Result> v;
    const bool mentionQuery = text.startsWith(QLatin1Char('@'));
    for (const Result &r : all)
        if (!mentionQuery || r.kind == QLatin1String("mention") || r.kind == QLatin1String("ask")) v << r;   // "@par" is about agents, not a window titled alex@taos
    for (Result &r : v) r.score *= kindWeight(r.kind);
    std::stable_sort(v.begin(), v.end(), [](const Result &a, const Result &b) { return a.score > b.score; });
    QList<Row> out;
    int top = -1;
    if (!suggestions) {
        for (int i = 0; i < v.size(); ++i)
            if (v[i].kind != QLatin1String("ask") && v[i].kind != QLatin1String("mention") && v[i].score >= 0.3) { top = i; break; }
    }
    if (top >= 0) out << Row{QStringLiteral("Top hit"), v[top]};
    // group the rest
    QList<int> kinds;
    QStringList kindNames;
    for (int i = 0; i < v.size(); ++i) if (i != top && !kindNames.contains(v[i].kind)) kindNames << v[i].kind;
    std::sort(kindNames.begin(), kindNames.end(), [](const QString &a, const QString &b) { return sectionRank(a) < sectionRank(b); });
    for (const QString &k : kindNames) {
        int n = 0;
        for (int i = 0; i < v.size(); ++i) {
            if (i == top || v[i].kind != k) continue;
            if (n++ >= capFor(k)) break;
            out << Row{sectionTitle(k, suggestions), v[i]};
        }
    }
    return out;
}

SearchEngine::SearchEngine(QObject *parent) : QObject(parent), m_pool(new QThreadPool) {
    m_pool->setMaxThreadCount(6);
    qRegisterMetaType<QList<taos::search::Row>>();
}

SearchEngine::~SearchEngine() {
    m_run.token.cancel();
    m_pool->clear();
    m_pool->waitForDone(2000);
}

void SearchEngine::addProvider(std::shared_ptr<Provider> p) { m_providers << std::move(p); }

void SearchEngine::prepare() {
    for (const auto &p : m_providers) if (p->available()) p->prepare();
}

void SearchEngine::cancel() {
    m_run.token.cancel();
    m_run.finalSent = true;
    ++m_gen;
}

int SearchEngine::search(const QString &text) {
    m_run.token.cancel();   // the previous search's providers stop polling
    m_run = Run();
    m_run.gen = ++m_gen;
    m_run.text = text;
    const int gen = m_run.gen;
    const CancelToken token = m_run.token;
    for (const auto &p : m_providers) {
        if (!p->available()) continue;
        m_run.pending << p->id();
        const QString pid = p->id();
        std::shared_ptr<Provider> prov = p;
        QPointer<SearchEngine> self(this);
        m_pool->start([self, prov, pid, gen, text, token] {
            const QList<Result> res = prov->query(text, token);
            if (token.cancelled()) return;
            if (!self) return;
            QMetaObject::invokeMethod(self.data(), [self, gen, pid, res] { if (self) self->deliver(gen, pid, res); }, Qt::QueuedConnection);
        });
        const int budget = qMax(1, int(p->timeBudgetMs() * m_scale));
        QTimer::singleShot(budget, this, [this, gen, pid] { timedOut(gen, pid); });
    }
    if (m_run.pending.isEmpty()) emitNow(m_run);
    return gen;
}

void SearchEngine::deliver(int gen, const QString &pid, QList<Result> res) {
    if (gen != m_run.gen || !m_run.pending.contains(pid)) return;   // stale, or already timed out
    m_run.pending.removeAll(pid);
    m_run.got.insert(pid, std::move(res));
    emitNow(m_run);
}

void SearchEngine::timedOut(int gen, const QString &pid) {
    if (gen != m_run.gen || !m_run.pending.contains(pid)) return;
    m_run.pending.removeAll(pid);   // its late answer is dropped by deliver()
    emit providerTimedOut(pid);
    emitNow(m_run);
}

void SearchEngine::emitNow(Run &r) {
    QList<Result> all;
    for (auto it = r.got.cbegin(); it != r.got.cend(); ++it) all += it.value();
    const bool fin = r.pending.isEmpty();
    emit updated(r.gen, arrange(all, r.text), fin);
}

}  // namespace taos::search
