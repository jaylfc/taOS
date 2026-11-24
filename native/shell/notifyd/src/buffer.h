// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// The local write-ahead buffer: a notification the daemon accepted is on disk
// (file written, fsynced, renamed into place, directory fsynced) BEFORE Notify
// returns, and is deleted only when the controller has confirmed it.
//
// Layout of the directory (mode 0700, files 0600):
//   <seq, 20 digits>-<n|c>.json   one pending record (c = critical, n = the rest)
//   rejected/<same name>          refused for good by the controller (kept, with a warning record)
//   dropped.jsonl                 full copies of records dropped on overflow, written BEFORE the drop
//   seq                           high-water mark of the id counter
#include <QDir>
#include <QJsonObject>
#include <QList>
#include <QMap>
#include <QString>

namespace Taos::Notifyd {

class WriteAheadBuffer
{
public:
    struct Options
    {
        QString dir;
        int maxNonCritical = 500; ///< over this the oldest non-critical records are dropped (after a warning record)
        int dropBatch = 50;       ///< how many are dropped at once, so one warning covers them
        int hardMax = 5000;       ///< total pending records; beyond it append() refuses (the app sees an error)
    };
    struct Entry
    {
        quint64 seq = 0;
        bool critical = false;
        QString path;
        QJsonObject record;
    };
    struct AppendResult
    {
        bool ok = false;
        QString error;
        int dropped = 0; ///< non-critical records dropped by this call (a warning record was written first)
    };

    explicit WriteAheadBuffer(const Options &o);
    /// Creates the directory, removes half-written temp files. False with `error` on failure.
    bool open(QString *error);
    /// The next sequence number. Persistent: never repeats after a restart. False (with `error`) when the
    /// counter cannot be made durable: the caller must then fail the Notify, never carry on with a number
    /// that could repeat.
    bool nextSeq(quint64 *seq, QString *error);
    /// The id the controller dedupes on: "n<install id>.<boot id>.<seq>". The install id is random, made once
    /// per state directory (so a wiped directory starts a new one); the boot id is random per run.
    QString clientId(quint64 seq) const;
    QString installId() const { return m_install; }
    /// Writes `record` durably under `seq`. When the buffer is over its cap, drops the oldest
    /// non-critical records, but only after the warning record is on disk.
    AppendResult append(quint64 seq, bool critical, const QJsonObject &record);
    /// Oldest first.
    QList<Entry> pending() const;
    int count() const;
    /// The controller confirmed it: delete. False when the file could not be removed.
    bool confirm(quint64 seq);
    /// The controller refused it for good: move to rejected/. A record is never just deleted.
    bool quarantine(quint64 seq);
    QString dir() const { return m_o.dir; }

private:
    QString nameFor(quint64 seq, bool critical) const;
    bool writeFileDurably(const QString &path, const QByteArray &bytes, QString *error) const;
    bool syncDir(const QString &dir) const;
    bool ensureInstallId(QString *error);
    void loadIndex();

    Options m_o;
    QMap<quint64, Entry> m_index; ///< what is pending, in memory after start-up
    QString m_install, m_boot;
    quint64 m_next = 1;
    quint64 m_highWater = 0;
};

} // namespace Taos::Notifyd
