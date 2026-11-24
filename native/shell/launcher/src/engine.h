// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QObject>
#include <QThreadPool>
#include <QTimer>
#include <memory>

#include "provider.h"

namespace taos::search {

// One line of the result list: a result and the section label it sits under ("Top hit", "Apps"...).
struct Row {
    QString section;
    Result r;
};

// Merge results of all providers into the display order: the best result becomes the "Top hit",
// the rest are grouped by kind in a fixed order, each group capped. `text` empty = suggestions.
QList<Row> arrange(const QList<Result> &all, const QString &text);
double kindWeight(const QString &kind);

// Runs the providers for each search on worker threads, time-boxed one by one. Results stream in:
// `updated` fires every time a provider delivers, with the merge of everything so far.
class SearchEngine : public QObject {
    Q_OBJECT
public:
    explicit SearchEngine(QObject *parent = nullptr);
    ~SearchEngine() override;
    void addProvider(std::shared_ptr<Provider> p);
    QList<std::shared_ptr<Provider>> providers() const { return m_providers; }
    void prepare();                 // the launcher is about to show
    int search(const QString &text);   // returns the generation number
    void cancel();
    int generation() const { return m_gen; }
    // Scale all time boxes (tests use small values).
    void setTimeScale(double s) { m_scale = s; }
signals:
    void updated(int generation, const QList<taos::search::Row> &rows, bool final);
    void providerTimedOut(const QString &providerId);
private:
    struct Run {
        int gen = 0;
        QString text;
        CancelToken token;
        QHash<QString, QList<Result>> got;
        QStringList pending;
        bool finalSent = false;
    };
    void deliver(int gen, const QString &pid, QList<Result> res);
    void timedOut(int gen, const QString &pid);
    void emitNow(Run &r);
    QList<std::shared_ptr<Provider>> m_providers;
    std::unique_ptr<QThreadPool> m_pool;
    Run m_run;
    int m_gen = 0;
    double m_scale = 1.0;
};

}  // namespace taos::search

Q_DECLARE_METATYPE(taos::search::Row)
Q_DECLARE_METATYPE(QList<taos::search::Row>)
