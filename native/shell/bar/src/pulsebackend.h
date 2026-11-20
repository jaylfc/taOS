#pragma once
#include <QByteArray>
#include <QHash>
#include <QMutex>
#include <QTimer>

#include "audiobackend.h"

struct pa_threaded_mainloop;
struct pa_context;
struct pa_stream;
struct pa_cvolume;

// libpulse on its own thread. Every change comes from pa_context_subscribe
// events: no timer asks the server anything. (The one timer here only runs
// while the connection is down, to try again.)
class PulseBackend : public AudioBackend {
    Q_OBJECT
public:
    // `server` empty: the default (PULSE_SERVER or the user's socket).
    explicit PulseBackend(const QString &server = QString(), QObject *parent = nullptr);
    ~PulseBackend() override;
    const AudioState &state() const override { return m_state; }
    bool connected() const { return m_state.connected; }
    int queryCount() const { return m_queries; }  // refreshes done so far (tests: must not grow while idle)
    bool retryTimerActive() const { return m_retry.isActive(); }

    void setDefault(bool sink, const QString &name) override;
    void setVolume(bool sink, quint32 index, int percent) override;
    void setMute(bool sink, quint32 index, bool mute) override;
    void setPort(bool sink, quint32 index, const QString &port) override;
    void setCardProfile(quint32 card, const QString &profile) override;
    void startMeter(const QString &sourceName) override;
    void stopMeter() override;

    struct Gather;  // one refresh in flight (defined in the .cpp)

private slots:
    void connectToServer();
    void doRefresh();

private:
    friend struct PulseCallbacks;
    void scheduleRefresh();
    void applyGathered(Gather *g);
    void connectionLost();
    void report(const QString &what);

    QString m_server;
    pa_threaded_mainloop *m_loop = nullptr;
    pa_context *m_ctx = nullptr;
    pa_stream *m_meter = nullptr;
    AudioState m_state;
    QTimer m_refresh, m_retry;
    QMutex m_volMutex;
    QHash<quint64, QByteArray> m_cvol;              // raw pa_cvolume per device
    int m_queries = 0;
    bool m_tearingDown = false;
};
