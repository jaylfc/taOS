#include "pulsebackend.h"

#include <pulse/pulseaudio.h>

#include <QHash>
#include <QPointer>
#include <QSharedPointer>
#include <cmath>
#include <cstring>

namespace {
struct Lock {
    explicit Lock(pa_threaded_mainloop *l) : m(l) { if (m) pa_threaded_mainloop_lock(m); }
    ~Lock() { if (m) pa_threaded_mainloop_unlock(m); }
    pa_threaded_mainloop *m;
};
quint64 key(bool sink, quint32 index) { return (quint64(sink ? 1 : 0) << 32) | index; }
QString s(const char *c) { return c ? QString::fromUtf8(c) : QString(); }
Avail av(int a) { return a == PA_PORT_AVAILABLE_NO ? Avail::No : a == PA_PORT_AVAILABLE_YES ? Avail::Yes : Avail::Unknown; }
int percentOf(const pa_cvolume &v) { return int(std::lround(double(pa_cvolume_max(&v)) * 100.0 / double(PA_VOLUME_NORM))); }
}  // namespace

// A refresh in flight: the callbacks run on the libpulse thread and fill this,
// the last one hands it to the Qt thread.
struct PulseBackend::Gather {
    PulseBackend *self = nullptr;
    int pending = 0;
    AudioState st;
    QHash<quint64, QByteArray> cvols;
};

struct PulseCallbacks {
    static void done(PulseBackend::Gather *g) {
        if (--g->pending > 0) return;
        PulseBackend *self = g->self;
        QPointer<PulseBackend> guard(self);
        auto *owned = g;
        QMetaObject::invokeMethod(self, [guard, owned] {
            if (guard) guard->applyGathered(owned);
            delete owned;
        }, Qt::QueuedConnection);
    }
    static void server(pa_context *, const pa_server_info *i, void *u) {
        auto *g = static_cast<PulseBackend::Gather *>(u);
        if (i) { g->st.defaultSink = s(i->default_sink_name); g->st.defaultSource = s(i->default_source_name); }
        done(g);
    }
    static void card(pa_context *, const pa_card_info *i, int eol, void *u) {
        auto *g = static_cast<PulseBackend::Gather *>(u);
        if (eol > 0) { done(g); return; }
        if (!i) return;
        AudioCard c;
        c.index = i->index;
        c.name = s(i->name);
        c.description = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_DESCRIPTION));
        c.bus = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_BUS));
        c.formFactor = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_FORM_FACTOR));
        c.vendor = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_VENDOR_NAME));
        c.product = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_PRODUCT_NAME));
        c.bluetooth = c.bus == QLatin1String("bluetooth") || c.name.startsWith(QLatin1String("bluez_card"));
        c.activeProfile = i->active_profile2 ? s(i->active_profile2->name) : QString();
        for (uint32_t k = 0; k < i->n_profiles; ++k) {
            const pa_card_profile_info2 *p = i->profiles2[k];
            AudioProfile ap;
            ap.name = s(p->name); ap.description = s(p->description);
            ap.nSinks = int(p->n_sinks); ap.nSources = int(p->n_sources);
            ap.priority = int(p->priority); ap.available = p->available != 0;
            c.profiles << ap;
        }
        g->st.cards << c;
    }
    template <typename Info>
    static void fillCommon(AudioDevice &d, const Info *i, bool sink, PulseBackend::Gather *g) {
        d.isSink = sink;
        d.index = i->index;
        d.name = s(i->name);
        d.description = s(i->description);
        d.card = i->card;
        d.mute = i->mute;
        d.volume = percentOf(i->volume);
        d.bus = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_BUS));
        d.formFactor = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_FORM_FACTOR));
        d.product = s(pa_proplist_gets(i->proplist, PA_PROP_DEVICE_PRODUCT_NAME));
        g->cvols.insert(key(sink, i->index), QByteArray(reinterpret_cast<const char *>(&i->volume), sizeof(pa_cvolume)));
    }
    static void sink(pa_context *, const pa_sink_info *i, int eol, void *u) {
        auto *g = static_cast<PulseBackend::Gather *>(u);
        if (eol > 0) { done(g); return; }
        if (!i) return;
        AudioDevice d;
        fillCommon(d, i, true, g);
        d.activePort = i->active_port ? s(i->active_port->name) : QString();
        for (uint32_t k = 0; k < i->n_ports; ++k) {
            const pa_sink_port_info *p = i->ports[k];
            d.ports << AudioPort{s(p->name), s(p->description), QString::number(int(p->type)), int(p->priority), av(p->available)};
            if (p->type == PA_DEVICE_PORT_TYPE_HDMI) d.ports.last().type = QStringLiteral("hdmi");
        }
        g->st.sinks << d;
    }
    static void source(pa_context *, const pa_source_info *i, int eol, void *u) {
        auto *g = static_cast<PulseBackend::Gather *>(u);
        if (eol > 0) { done(g); return; }
        if (!i) return;
        AudioDevice d;
        fillCommon(d, i, false, g);
        d.monitor = i->monitor_of_sink != PA_INVALID_INDEX;
        d.activePort = i->active_port ? s(i->active_port->name) : QString();
        for (uint32_t k = 0; k < i->n_ports; ++k) {
            const pa_source_port_info *p = i->ports[k];
            d.ports << AudioPort{s(p->name), s(p->description), QString(), int(p->priority), av(p->available)};
        }
        g->st.sources << d;
    }
    static void sinkInput(pa_context *, const pa_sink_input_info *i, int eol, void *u) {
        auto *g = static_cast<PulseBackend::Gather *>(u);
        if (eol > 0) { done(g); return; }
        if (!i) return;
        g->st.streams << AudioStream{i->index, i->sink, s(i->name)};
    }
};

PulseBackend::PulseBackend(const QString &server, QObject *parent) : AudioBackend(parent), m_server(server) {
    m_refresh.setSingleShot(true);
    m_refresh.setInterval(40);  // coalesces a burst of events into one query
    connect(&m_refresh, &QTimer::timeout, this, &PulseBackend::doRefresh);
    m_retry.setSingleShot(true);
    m_retry.setInterval(3000);
    connect(&m_retry, &QTimer::timeout, this, &PulseBackend::connectToServer);
    m_loop = pa_threaded_mainloop_new();
    pa_threaded_mainloop_start(m_loop);
    connectToServer();
}

PulseBackend::~PulseBackend() {
    m_tearingDown = true;
    stopMeter();
    if (m_loop) {
        {
            Lock l(m_loop);
            if (m_ctx) {
                pa_context_set_state_callback(m_ctx, nullptr, nullptr);
                pa_context_set_subscribe_callback(m_ctx, nullptr, nullptr);
                pa_context_disconnect(m_ctx);
                pa_context_unref(m_ctx);
                m_ctx = nullptr;
            }
        }
        pa_threaded_mainloop_stop(m_loop);
        pa_threaded_mainloop_free(m_loop);
    }
}

void PulseBackend::connectToServer() {
    Lock l(m_loop);
    if (m_ctx) { pa_context_unref(m_ctx); m_ctx = nullptr; }
    pa_proplist *pl = pa_proplist_new();
    pa_proplist_sets(pl, PA_PROP_APPLICATION_NAME, "taos-bar");
    pa_proplist_sets(pl, PA_PROP_APPLICATION_ID, "my.taos.bar");
    m_ctx = pa_context_new_with_proplist(pa_threaded_mainloop_get_api(m_loop), "taos-bar", pl);
    pa_proplist_free(pl);
    pa_context_set_state_callback(m_ctx, [](pa_context *c, void *u) {
        auto *self = static_cast<PulseBackend *>(u);
        const pa_context_state_t st = pa_context_get_state(c);
        QPointer<PulseBackend> guard(self);
        if (st == PA_CONTEXT_READY) {
            pa_context_set_subscribe_callback(c, [](pa_context *, pa_subscription_event_type_t, uint32_t, void *u2) {
                auto *me = static_cast<PulseBackend *>(u2);
                QMetaObject::invokeMethod(me, [me] { me->scheduleRefresh(); }, Qt::QueuedConnection);
            }, self);
            pa_operation *o = pa_context_subscribe(c, pa_subscription_mask_t(PA_SUBSCRIPTION_MASK_SINK | PA_SUBSCRIPTION_MASK_SOURCE | PA_SUBSCRIPTION_MASK_CARD
                                                                              | PA_SUBSCRIPTION_MASK_SINK_INPUT | PA_SUBSCRIPTION_MASK_SERVER), nullptr, nullptr);
            if (o) pa_operation_unref(o);
            QMetaObject::invokeMethod(self, [self] { self->scheduleRefresh(); }, Qt::QueuedConnection);
        } else if (st == PA_CONTEXT_FAILED || st == PA_CONTEXT_TERMINATED) {
            QMetaObject::invokeMethod(self, [guard] { if (guard) guard->connectionLost(); }, Qt::QueuedConnection);
        }
    }, this);
    const QByteArray srv = m_server.toUtf8();
    if (pa_context_connect(m_ctx, srv.isEmpty() ? nullptr : srv.constData(), PA_CONTEXT_NOFLAGS, nullptr) < 0)
        QMetaObject::invokeMethod(this, [this] { connectionLost(); }, Qt::QueuedConnection);
}

void PulseBackend::connectionLost() {
    if (m_tearingDown) return;
    if (m_state.connected || m_state.sinks.size() || m_state.sources.size()) {
        m_state = AudioState();
        emit stateChanged();
    }
    if (!m_retry.isActive()) m_retry.start();  // only while the server is away
}

void PulseBackend::scheduleRefresh() {
    if (!m_refresh.isActive()) m_refresh.start();
}

void PulseBackend::doRefresh() {
    Lock l(m_loop);
    if (!m_ctx || pa_context_get_state(m_ctx) != PA_CONTEXT_READY) return;
    ++m_queries;
    auto *g = new Gather;
    g->self = this;
    g->pending = 5;
    g->st.connected = true;
    auto drop = [](pa_operation *o) { if (o) pa_operation_unref(o); };
    drop(pa_context_get_server_info(m_ctx, &PulseCallbacks::server, g));
    drop(pa_context_get_card_info_list(m_ctx, &PulseCallbacks::card, g));
    drop(pa_context_get_sink_info_list(m_ctx, &PulseCallbacks::sink, g));
    drop(pa_context_get_source_info_list(m_ctx, &PulseCallbacks::source, g));
    drop(pa_context_get_sink_input_info_list(m_ctx, &PulseCallbacks::sinkInput, g));
}

void PulseBackend::applyGathered(Gather *g) {
    {
        QMutexLocker lk(&m_volMutex);
        m_cvol = g->cvols;
    }
    m_state = g->st;
    m_retry.stop();
    emit stateChanged();
}

void PulseBackend::report(const QString &what) { emit failed(what); }

namespace {
struct OpCtx {
    QPointer<PulseBackend> self;
    QString what;
};
void successCb(pa_context *c, int ok, void *u) {
    auto *o = static_cast<OpCtx *>(u);
    if (!ok && o->self) {
        const QString msg = QString::fromUtf8(pa_strerror(pa_context_errno(c)));
        QPointer<PulseBackend> p = o->self;
        const QString what = o->what + QStringLiteral(": ") + msg;
        QMetaObject::invokeMethod(p.data(), [p, what] { if (p) emit p->failed(what); }, Qt::QueuedConnection);
    }
    delete o;
}
}  // namespace

#define OP(text) new OpCtx{QPointer<PulseBackend>(this), QStringLiteral(text)}

void PulseBackend::setDefault(bool sink, const QString &name) {
    Lock l(m_loop);
    if (!m_ctx) return;
    const QByteArray n = name.toUtf8();
    pa_operation *o = sink ? pa_context_set_default_sink(m_ctx, n.constData(), successCb, OP("set default output"))
                           : pa_context_set_default_source(m_ctx, n.constData(), successCb, OP("set default input"));
    if (o) pa_operation_unref(o);
    if (sink) {
        // Streams already playing follow the new default: move each one by index.
        quint32 target = kNoIndex;
        for (const AudioDevice &d : m_state.sinks)
            if (d.name == name) target = d.index;
        if (target != kNoIndex)
            for (const AudioStream &st : m_state.streams)
                if (st.device != target) {
                    pa_operation *m = pa_context_move_sink_input_by_index(m_ctx, st.index, target, successCb, OP("move playing stream"));
                    if (m) pa_operation_unref(m);
                }
    } else {
        quint32 target = kNoIndex;
        for (const AudioDevice &d : m_state.sources)
            if (d.name == name) target = d.index;
        (void)target;  // recording streams follow the default source in PipeWire; nothing to move by hand
    }
}

void PulseBackend::setVolume(bool sink, quint32 index, int percent) {
    Lock l(m_loop);
    if (!m_ctx) return;
    pa_cvolume cv;
    {
        QMutexLocker lk(&m_volMutex);
        const QByteArray raw = m_cvol.value(key(sink, index));
        if (raw.size() == int(sizeof(pa_cvolume))) std::memcpy(&cv, raw.constData(), sizeof(cv));
        else pa_cvolume_set(&cv, 2, PA_VOLUME_NORM);
    }
    const pa_volume_t v = pa_volume_t(std::lround(double(PA_VOLUME_NORM) * std::max(0, std::min(100, percent)) / 100.0));
    if (pa_cvolume_max(&cv) == 0) pa_cvolume_set(&cv, cv.channels ? cv.channels : 2, v);
    else pa_cvolume_scale(&cv, v);  // keeps the balance between channels
    pa_operation *o = sink ? pa_context_set_sink_volume_by_index(m_ctx, index, &cv, successCb, OP("set volume"))
                           : pa_context_set_source_volume_by_index(m_ctx, index, &cv, successCb, OP("set input volume"));
    if (o) pa_operation_unref(o);
}

void PulseBackend::setMute(bool sink, quint32 index, bool mute) {
    Lock l(m_loop);
    if (!m_ctx) return;
    pa_operation *o = sink ? pa_context_set_sink_mute_by_index(m_ctx, index, mute, successCb, OP("mute"))
                           : pa_context_set_source_mute_by_index(m_ctx, index, mute, successCb, OP("mute input"));
    if (o) pa_operation_unref(o);
}

void PulseBackend::setPort(bool sink, quint32 index, const QString &port) {
    Lock l(m_loop);
    if (!m_ctx) return;
    QByteArray name;
    for (const AudioDevice &d : sink ? m_state.sinks : m_state.sources)
        if (d.index == index) name = d.name.toUtf8();
    if (name.isEmpty()) return;
    const QByteArray p = port.toUtf8();
    pa_operation *o = sink ? pa_context_set_sink_port_by_name(m_ctx, name.constData(), p.constData(), successCb, OP("switch output"))
                           : pa_context_set_source_port_by_name(m_ctx, name.constData(), p.constData(), successCb, OP("switch input"));
    if (o) pa_operation_unref(o);
}

void PulseBackend::setCardProfile(quint32 card, const QString &profile) {
    Lock l(m_loop);
    if (!m_ctx) return;
    const QByteArray p = profile.toUtf8();
    pa_operation *o = pa_context_set_card_profile_by_index(m_ctx, card, p.constData(), successCb, OP("switch profile"));
    if (o) pa_operation_unref(o);
}

void PulseBackend::startMeter(const QString &sourceName) {
    stopMeter();
    Lock l(m_loop);
    if (!m_ctx || pa_context_get_state(m_ctx) != PA_CONTEXT_READY) return;
    pa_sample_spec ss;
    ss.format = PA_SAMPLE_FLOAT32LE;
    ss.rate = 25;  // peak detect delivers one value per sample
    ss.channels = 1;
    m_meter = pa_stream_new(m_ctx, "taos-bar level", &ss, nullptr);
    if (!m_meter) return;
    pa_stream_set_read_callback(m_meter, [](pa_stream *st, size_t, void *u) {
        auto *self = static_cast<PulseBackend *>(u);
        const void *data = nullptr;
        size_t n = 0;
        if (pa_stream_peek(st, &data, &n) < 0) return;
        float peak = 0;
        if (data && n >= sizeof(float)) std::memcpy(&peak, static_cast<const char *>(data) + n - sizeof(float), sizeof(float));
        if (n) pa_stream_drop(st);
        QPointer<PulseBackend> guard(self);
        const double lvl = std::max(0.0, std::min(1.0, double(peak)));
        QMetaObject::invokeMethod(self, [guard, lvl] { if (guard) emit guard->meterLevel(lvl); }, Qt::QueuedConnection);
    }, this);
    pa_buffer_attr attr;
    std::memset(&attr, 0xFF, sizeof(attr));
    attr.fragsize = sizeof(float);
    const QByteArray src = sourceName.toUtf8();
    pa_stream_connect_record(m_meter, src.isEmpty() ? nullptr : src.constData(), &attr,
                             pa_stream_flags_t(PA_STREAM_DONT_MOVE | PA_STREAM_ADJUST_LATENCY | PA_STREAM_PEAK_DETECT | PA_STREAM_DONT_INHIBIT_AUTO_SUSPEND));
}

void PulseBackend::stopMeter() {
    if (!m_loop) return;
    Lock l(m_loop);
    if (!m_meter) return;
    pa_stream_set_read_callback(m_meter, nullptr, nullptr);
    pa_stream_disconnect(m_meter);
    pa_stream_unref(m_meter);
    m_meter = nullptr;
}
