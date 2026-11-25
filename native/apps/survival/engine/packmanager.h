// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Content packs: the catalogue (a JSON manifest in the repository), the packs on this device, and the work between the
// two: download with resume, a checksum check that refuses a bad file, delete, and import of a file the person already
// has. Packs live in the person's data folder, never in the repository.
#include <QHash>
#include <QJsonObject>
#include <QList>
#include <QNetworkAccessManager>
#include <QObject>
#include <QPointer>
#include <QString>
#include <QUrl>

class QFile;
class QNetworkReply;

namespace Taos::Survival {

struct PackInfo
{
    QString id;
    QString title;
    QString description;
    QString kind;              // zim | map
    QString language;
    QString region;
    QString file;              // file name inside the packs folder
    QUrl url;                  // empty: there is no publisher download (see `recipe`)
    QString recipe;            // how to make the pack when there is no download address
    qint64 bytes = 0;          // download size
    QString sha256;            // the publisher's checksum, lower case hex
    QString licence;           // SPDX id or a short name
    QString licenceUrl;
    QString attribution;
    QString publisher;
    QString version;
    bool needsAcceptance = false;
    // device state
    bool installed = false;
    bool local = false;        // a file the catalogue does not know (imported or copied in by hand)
    QString path;              // full path when installed
    qint64 installedBytes = 0;
    QString state;             // available | downloading | verifying | installed | failed
    qint64 received = 0;
    QString error;             // why the last attempt failed, in words
    bool licenceAccepted = false;
};

enum class VerifyResult { Ok, Mismatch, Unreadable, NoChecksum };

class PackManager : public QObject
{
    Q_OBJECT
public:
    /// `dataDir` is the survival folder (packs go in <dataDir>/packs).
    explicit PackManager(const QString &dataDir, QObject *parent = nullptr);
    QString packsDir() const { return m_dir + QStringLiteral("/packs"); }

    bool loadCatalogue(const QByteArray &json, QString *error = nullptr);
    bool loadCatalogueFile(const QString &path, QString *error = nullptr);
    /// Re-reads the packs folder (call after copying a file in by hand).
    void rescan();

    QList<PackInfo> packs() const;
    PackInfo pack(const QString &id) const;
    bool contains(const QString &id) const { return m_catalogue.contains(id) || m_local.contains(id); }

    /// Starts (or resumes) a download. Returns false and sets `error` when it cannot start: unknown pack, no
    /// publisher address, no checksum, licence not accepted, not enough free space.
    bool download(const QString &id, QString *error = nullptr);
    void cancel(const QString &id);
    /// Hashes the installed file again and compares it with the catalogue checksum (or the one recorded at install).
    VerifyResult verifyInstalled(const QString &id) const;
    static VerifyResult verifyFile(const QString &file, const QString &expectedSha256);
    static QString sha256Of(const QString &file);
    /// Deletes the pack's file, any partial download and its record. Returns false (and says why) if something stays.
    bool remove(const QString &id, QString *error = nullptr);
    /// Copies a file into the packs folder as a local pack, verifying it against `expectedSha256` when given.
    bool importFile(const QString &source, const QString &expectedSha256, QString *error = nullptr);
    void acceptLicence(const QString &id);
    bool licenceAccepted(const QString &id) const;
    /// A short summary for the CLI and agents.
    QJsonObject toJson(const PackInfo &p) const;

Q_SIGNALS:
    void changed();                                   // the list or a state changed
    void progress(const QString &id, qint64 received, qint64 total);
    void finished(const QString &id, bool ok, const QString &message);

private:
    struct Job
    {
        QPointer<QNetworkReply> reply;
        QFile *file = nullptr;
        qint64 resumeFrom = 0;
        qint64 total = 0;
        QString partPath;
    };
    QString recordPath(const QString &file) const { return packsDir() + QLatin1Char('/') + file + QStringLiteral(".taospack.json"); }
    void startRequest(const QString &id);
    void finishDownload(const QString &id);
    void failJob(const QString &id, const QString &message);
    void writeRecord(const PackInfo &p, const QString &sha) const;
    QString m_dir;
    QHash<QString, PackInfo> m_catalogue;
    QStringList m_order;
    QHash<QString, PackInfo> m_local;
    QHash<QString, Job> m_jobs;
    QHash<QString, QString> m_errors;
    QHash<QString, QString> m_state;     // verifying | failed
    QNetworkAccessManager m_nam;
};

} // namespace Taos::Survival
