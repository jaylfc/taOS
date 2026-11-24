#pragma once
#include <QHash>
#include <QMutex>
#include <QString>

namespace taos::search {

// Per person usage memory: how often and how recently each result id was chosen. The score
// halves every 14 days, each pick adds 1, so a thing used daily outranks one used ten times last year.
// Stored in ~/.local/state/taos/launcher.json ($XDG_STATE_HOME). Thread safe.
class Frecency {
public:
    explicit Frecency(const QString &path = defaultPath());
    static QString defaultPath();
    void record(const QString &id, qint64 nowSecs = 0);
    // 0 (never used) .. 1 (used a lot, lately)
    double boost(const QString &id, qint64 nowSecs = 0) const;
    // Ids by descending frecency, at most `n`, only those with some boost.
    QStringList top(const QString &prefix, int n, qint64 nowSecs = 0) const;
    void save() const;
    QString path() const { return m_path; }
private:
    struct Entry { double score = 0; qint64 at = 0; };
    static double decayed(const Entry &e, qint64 now);
    void load();
    QString m_path;
    mutable QMutex m_mu;
    QHash<QString, Entry> m_e;
};

}  // namespace taos::search
