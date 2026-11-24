// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QHash>
#include <QImage>
#include <QObject>
#include <QSet>
#include <QSize>
#include <QString>
#include <atomic>
#include <memory>
#include <mutex>
#include <thread>

// Live window thumbnails over ext-foreign-toplevel-list-v1 and ext-image-copy-capture-v1.
//
// Its own Wayland connection and thread: sessions are created only while `setTargets` holds a
// non-empty map (the overlay is visible), and are destroyed again when it goes empty, so a hidden
// overlay costs one poll() that never wakes. Frames are read from wl_shm buffers and scaled down on
// that thread; only the finished thumbnail-size QImage crosses to the GUI thread.
class CaptureWorker : public QObject {
    Q_OBJECT
public:
    explicit CaptureWorker(QObject *parent = nullptr);
    ~CaptureWorker() override;
    // Connects and starts the thread. False (with `error`) when the compositor lacks a protocol.
    bool start(QString *error);
    bool available() const { return m_available; }
    // identifier -> largest useful thumbnail size in device pixels. Empty stops all capturing.
    void setTargets(const QHash<QString, QSize> &targets);
    void setMaxFps(int fps) { m_maxFps = fps; }
    // Windows the compositor currently lists (identifiers); updated from the thread.
    QSet<QString> knownIdentifiers() const;
    // Frames delivered since the last call, per identifier (for measuring fps).
    QHash<QString, int> takeFrameCounts();
signals:
    void frame(const QString &identifier, const QImage &image);
    void toplevelsChanged();
private:
    struct Impl;
    void run();
    std::unique_ptr<Impl> d;
    std::thread m_thread;
    std::atomic<bool> m_available { false };
    std::atomic<int> m_maxFps { 0 };
    std::atomic<int> m_inflight { 0 };
    friend struct Impl;
public:
    void frameConsumed() { m_inflight.fetch_sub(1); }
};
