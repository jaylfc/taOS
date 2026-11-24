// SPDX-License-Identifier: AGPL-3.0-or-later
#include "capture.h"

#include <QMetaObject>
#include <algorithm>
#include <chrono>
#include <cstring>
#include <map>
#include <poll.h>
#include <sys/eventfd.h>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>
#include <wayland-client.h>

#include "ext-foreign-toplevel-list-v1-client-protocol.h"
#include "ext-image-capture-source-v1-client-protocol.h"
#include "ext-image-copy-capture-v1-client-protocol.h"

using Clock = std::chrono::steady_clock;

namespace {
qint64 nowMs() { return std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now().time_since_epoch()).count(); }

struct Toplevel;
struct Sess {
    Toplevel *tl = nullptr;
    ext_image_capture_source_v1 *src = nullptr;
    ext_image_copy_capture_session_v1 *session = nullptr;
    ext_image_copy_capture_frame_v1 *frame = nullptr;
    int w = 0, h = 0;
    uint32_t fmt = 0;
    std::vector<uint32_t> fmts;
    bool configured = false;
    bool busy = false;
    bool dead = false;       // `stopped` seen: destroy and (maybe) recreate
    qint64 due = 0;          // next capture time
    wl_shm_pool *pool = nullptr;
    wl_buffer *buf = nullptr;
    void *data = nullptr;
    size_t size = 0;
    int bw = 0, bh = 0;      // buffer geometry
    uint32_t bfmt = 0;
    int stride = 0;
};
struct Toplevel {
    ext_foreign_toplevel_handle_v1 *h = nullptr;
    QString identifier;
    bool done = false;
    Sess *sess = nullptr;
};

uint32_t pickFormat(const std::vector<uint32_t> &f) {
    static const uint32_t pref[] = { WL_SHM_FORMAT_ARGB8888, WL_SHM_FORMAT_XRGB8888,
                                     WL_SHM_FORMAT_ABGR8888, WL_SHM_FORMAT_XBGR8888 };
    for (uint32_t p : pref) if (std::find(f.begin(), f.end(), p) != f.end()) return p;
    return 0xFFFFFFFFu;
}
QImage::Format qtFormat(uint32_t f) {
    switch (f) {
    case WL_SHM_FORMAT_ARGB8888: return QImage::Format_ARGB32_Premultiplied;
    case WL_SHM_FORMAT_XRGB8888: return QImage::Format_RGB32;
    case WL_SHM_FORMAT_ABGR8888: return QImage::Format_RGBA8888_Premultiplied;
    case WL_SHM_FORMAT_XBGR8888: return QImage::Format_RGBX8888;
    }
    return QImage::Format_Invalid;
}
}

struct CaptureWorker::Impl {
    CaptureWorker *q = nullptr;
    wl_display *dpy = nullptr;
    wl_registry *reg = nullptr;
    wl_shm *shm = nullptr;
    ext_foreign_toplevel_list_v1 *list = nullptr;
    ext_foreign_toplevel_image_capture_source_manager_v1 *srcMgr = nullptr;
    ext_image_copy_capture_manager_v1 *copyMgr = nullptr;
    int evfd = -1;
    std::atomic<bool> quit { false };

    std::mutex mu;                       // guards the cross-thread fields below
    QHash<QString, QSize> targets;       // desired state, written by the GUI thread
    bool targetsDirty = false;
    QSet<QString> known;
    QHash<QString, int> counts;

    std::vector<Toplevel *> tops;
    std::map<QString, QSize> active;     // the thread's copy of targets

    void wake() { uint64_t one = 1; ssize_t r = ::write(evfd, &one, 8); (void)r; }

    int interval() const {
        const int n = int(active.size());
        int fps = n <= 4 ? 15 : n <= 8 ? 12 : n <= 16 ? 8 : 5;
        if (q->m_maxFps > 0) fps = std::min(fps, q->m_maxFps.load());
        return 1000 / fps;
    }

    // ---- sessions ----
    void freeBuffer(Sess *s) {
        if (s->buf) { wl_buffer_destroy(s->buf); s->buf = nullptr; }
        if (s->pool) { wl_shm_pool_destroy(s->pool); s->pool = nullptr; }
        if (s->data) { munmap(s->data, s->size); s->data = nullptr; }
        s->bw = s->bh = 0;
    }
    bool allocBuffer(Sess *s) {
        freeBuffer(s);
        s->stride = s->w * 4;
        s->size = size_t(s->stride) * size_t(s->h);
        const int fd = memfd_create("taos-overview-thumb", MFD_CLOEXEC);
        if (fd < 0) return false;
        if (ftruncate(fd, off_t(s->size)) != 0) { ::close(fd); return false; }
        void *p = mmap(nullptr, s->size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (p == MAP_FAILED) { ::close(fd); return false; }
        s->data = p;
        s->pool = wl_shm_create_pool(shm, fd, int(s->size));
        ::close(fd);
        s->buf = wl_shm_pool_create_buffer(s->pool, 0, s->w, s->h, s->stride, s->fmt);
        s->bw = s->w; s->bh = s->h; s->bfmt = s->fmt;
        return true;
    }
    void destroySession(Sess *s) {
        if (s->frame) ext_image_copy_capture_frame_v1_destroy(s->frame);
        if (s->session) ext_image_copy_capture_session_v1_destroy(s->session);
        if (s->src) ext_image_capture_source_v1_destroy(s->src);
        freeBuffer(s);
        if (s->tl) s->tl->sess = nullptr;
        delete s;
    }

    static void sBufferSize(void *d, ext_image_copy_capture_session_v1 *, uint32_t w, uint32_t h) {
        auto *s = static_cast<Sess *>(d); s->w = int(w); s->h = int(h);
    }
    static void sShmFormat(void *d, ext_image_copy_capture_session_v1 *, uint32_t f) {
        static_cast<Sess *>(d)->fmts.push_back(f);
    }
    static void sDmabufDevice(void *, ext_image_copy_capture_session_v1 *, wl_array *) {}
    static void sDmabufFormat(void *, ext_image_copy_capture_session_v1 *, uint32_t, wl_array *) {}
    static void sDone(void *d, ext_image_copy_capture_session_v1 *) {
        auto *s = static_cast<Sess *>(d);
        impl()->sessionConfigured(s);
        s->fmts.clear();
    }
    static void sStopped(void *d, ext_image_copy_capture_session_v1 *) { static_cast<Sess *>(d)->dead = true; }

    void sessionConfigured(Sess *s) {
        const uint32_t f = s->fmts.empty() ? s->fmt : pickFormat(s->fmts);
        if (f == 0xFFFFFFFFu || s->w <= 0 || s->h <= 0) return;
        s->fmt = f;
        if (!s->buf || s->bw != s->w || s->bh != s->h || s->bfmt != s->fmt) {
            if (!allocBuffer(s)) return;
        }
        s->configured = true;
        s->due = 0;
    }

    static void fTransform(void *, ext_image_copy_capture_frame_v1 *, uint32_t) {}
    static void fDamage(void *, ext_image_copy_capture_frame_v1 *, int32_t, int32_t, int32_t, int32_t) {}
    static void fPresentation(void *, ext_image_copy_capture_frame_v1 *, uint32_t, uint32_t, uint32_t) {}
    static void fReady(void *d, ext_image_copy_capture_frame_v1 *) { impl()->frameReady(static_cast<Sess *>(d)); }
    static void fFailed(void *d, ext_image_copy_capture_frame_v1 *, uint32_t reason) {
        impl()->frameFailed(static_cast<Sess *>(d), reason);
    }

    void endFrame(Sess *s) {
        if (s->frame) { ext_image_copy_capture_frame_v1_destroy(s->frame); s->frame = nullptr; }
        s->busy = false;
    }
    void frameReady(Sess *s) {
        const QString id = s->tl ? s->tl->identifier : QString();
        QSize tgt;
        auto it = active.find(id);
        if (it != active.end()) tgt = it->second;
        if (s->data && qtFormat(s->bfmt) != QImage::Format_Invalid) {
            // A view onto the shm buffer; `scaled` / `copy` read it on this thread only, before the next capture.
            const QImage view(static_cast<const uchar *>(s->data), s->bw, s->bh, s->stride, qtFormat(s->bfmt));
            QImage out;
            if (tgt.isValid() && (view.width() > tgt.width() || view.height() > tgt.height()))
                out = view.scaled(tgt, Qt::KeepAspectRatio, Qt::SmoothTransformation);
            else
                out = view.copy();
            {
                std::lock_guard<std::mutex> l(mu);
                counts[id] += 1;
            }
            if (q->m_inflight.load() < 16) {
                q->m_inflight.fetch_add(1);
                CaptureWorker *w = q;
                QMetaObject::invokeMethod(w, [w, id, out] { emit w->frame(id, out); }, Qt::QueuedConnection);
            }
        }
        endFrame(s);
        s->due = nowMs() + interval();
    }
    void frameFailed(Sess *s, uint32_t reason) {
        endFrame(s);
        if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_BUFFER_CONSTRAINTS) {
            s->configured = false;     // a new `done` with fresh constraints follows
        } else if (reason == EXT_IMAGE_COPY_CAPTURE_FRAME_V1_FAILURE_REASON_STOPPED) {
            s->dead = true;
        } else {
            s->due = nowMs() + 250;
        }
    }

    void startCapture(Sess *s) {
        s->frame = ext_image_copy_capture_session_v1_create_frame(s->session);
        static const ext_image_copy_capture_frame_v1_listener fl = { fTransform, fDamage, fPresentation, fReady, fFailed };
        ext_image_copy_capture_frame_v1_add_listener(s->frame, &fl, s);
        ext_image_copy_capture_frame_v1_attach_buffer(s->frame, s->buf);
        ext_image_copy_capture_frame_v1_damage_buffer(s->frame, 0, 0, s->w, s->h);
        ext_image_copy_capture_frame_v1_capture(s->frame);
        s->busy = true;
    }

    void createSession(Toplevel *t) {
        auto *s = new Sess;
        s->tl = t;
        t->sess = s;
        s->src = ext_foreign_toplevel_image_capture_source_manager_v1_create_source(srcMgr, t->h);
        s->session = ext_image_copy_capture_manager_v1_create_session(copyMgr, s->src, 0);
        static const ext_image_copy_capture_session_v1_listener sl = { sBufferSize, sShmFormat, sDmabufDevice,
                                                                        sDmabufFormat, sDone, sStopped };
        ext_image_copy_capture_session_v1_add_listener(s->session, &sl, s);
    }

    // ---- toplevels ----
    static void hClosed(void *d, ext_foreign_toplevel_handle_v1 *) { impl()->toplevelClosed(static_cast<Toplevel *>(d)); }
    static void hDone(void *d, ext_foreign_toplevel_handle_v1 *) {
        auto *t = static_cast<Toplevel *>(d);
        t->done = true;
        impl()->publishKnown();
    }
    static void hTitle(void *, ext_foreign_toplevel_handle_v1 *, const char *) {}
    static void hAppId(void *, ext_foreign_toplevel_handle_v1 *, const char *) {}
    static void hIdentifier(void *d, ext_foreign_toplevel_handle_v1 *, const char *id) {
        static_cast<Toplevel *>(d)->identifier = QString::fromUtf8(id);
    }
    static void lToplevel(void *, ext_foreign_toplevel_list_v1 *, ext_foreign_toplevel_handle_v1 *h) {
        auto *t = new Toplevel;
        t->h = h;
        static const ext_foreign_toplevel_handle_v1_listener hl = { hClosed, hDone, hTitle, hAppId, hIdentifier };
        ext_foreign_toplevel_handle_v1_add_listener(h, &hl, t);
        impl()->tops.push_back(t);
    }
    static void lFinished(void *, ext_foreign_toplevel_list_v1 *) {}

    void toplevelClosed(Toplevel *t) {
        if (t->sess) destroySession(t->sess);
        ext_foreign_toplevel_handle_v1_destroy(t->h);
        tops.erase(std::remove(tops.begin(), tops.end(), t), tops.end());
        delete t;
        publishKnown();
    }
    void publishKnown() {
        {
            std::lock_guard<std::mutex> l(mu);
            known.clear();
            for (Toplevel *t : tops) if (t->done && !t->identifier.isEmpty()) known.insert(t->identifier);
        }
        CaptureWorker *w = q;
        QMetaObject::invokeMethod(w, [w] { emit w->toplevelsChanged(); }, Qt::QueuedConnection);
    }

    // ---- registry ----
    static void rGlobal(void *, wl_registry *r, uint32_t name, const char *iface, uint32_t ver) {
        Impl *i = impl();
        if (!strcmp(iface, wl_shm_interface.name))
            i->shm = static_cast<wl_shm *>(wl_registry_bind(r, name, &wl_shm_interface, 1));
        else if (!strcmp(iface, ext_foreign_toplevel_list_v1_interface.name))
            i->list = static_cast<ext_foreign_toplevel_list_v1 *>(wl_registry_bind(r, name, &ext_foreign_toplevel_list_v1_interface, 1));
        else if (!strcmp(iface, ext_foreign_toplevel_image_capture_source_manager_v1_interface.name))
            i->srcMgr = static_cast<ext_foreign_toplevel_image_capture_source_manager_v1 *>(
                wl_registry_bind(r, name, &ext_foreign_toplevel_image_capture_source_manager_v1_interface, 1));
        else if (!strcmp(iface, ext_image_copy_capture_manager_v1_interface.name))
            i->copyMgr = static_cast<ext_image_copy_capture_manager_v1 *>(
                wl_registry_bind(r, name, &ext_image_copy_capture_manager_v1_interface, 1));
        (void)ver;
    }
    static void rRemove(void *, wl_registry *, uint32_t) {}

    static Impl *&cur() { static Impl *p = nullptr; return p; }
    static Impl *impl() { return cur(); }

    void applyTargets() {
        QHash<QString, QSize> t;
        {
            std::lock_guard<std::mutex> l(mu);
            if (!targetsDirty) return;
            t = targets;
            targetsDirty = false;
        }
        active.clear();
        for (auto it = t.begin(); it != t.end(); ++it) active[it.key()] = it.value();
        for (Toplevel *tl : tops) {
            if (!tl->done || tl->identifier.isEmpty()) continue;
            const bool want = active.count(tl->identifier) > 0;
            if (want && !tl->sess) createSession(tl);
            else if (!want && tl->sess) destroySession(tl->sess);
        }
    }

    void tick() {
        const qint64 now = nowMs();
        for (Toplevel *tl : tops) {
            // a toplevel that appeared after the targets were set
            if (!tl->sess && tl->done && !tl->identifier.isEmpty() && active.count(tl->identifier)) createSession(tl);
            Sess *s = tl->sess;
            if (!s) continue;
            if (s->dead) {
                destroySession(s);
                continue;     // recreated on the next applyTargets / tick
            }
            if (s->configured && !s->busy && s->due <= now) startCapture(s);
        }
    }
    int timeoutMs() {
        const qint64 now = nowMs();
        qint64 best = -1;
        for (Toplevel *tl : tops) {
            Sess *s = tl->sess;
            if (!s || !s->configured || s->busy) continue;
            const qint64 d = std::max<qint64>(0, s->due - now);
            if (best < 0 || d < best) best = d;
        }
        return best < 0 ? -1 : int(best);
    }
};

CaptureWorker::CaptureWorker(QObject *parent) : QObject(parent), d(new Impl) { d->q = this; }

CaptureWorker::~CaptureWorker() {
    if (m_thread.joinable()) {
        d->quit = true;
        d->wake();
        m_thread.join();
    }
}

bool CaptureWorker::start(QString *error) {
    Impl *i = d.get();
    i->dpy = wl_display_connect(nullptr);
    if (!i->dpy) { if (error) *error = QStringLiteral("cannot connect to the Wayland display"); return false; }
    Impl::cur() = i;
    i->reg = wl_display_get_registry(i->dpy);
    static const wl_registry_listener rl = { Impl::rGlobal, Impl::rRemove };
    wl_registry_add_listener(i->reg, &rl, nullptr);
    wl_display_roundtrip(i->dpy);
    QStringList missing;
    if (!i->shm) missing << QStringLiteral("wl_shm");
    if (!i->list) missing << QStringLiteral("ext_foreign_toplevel_list_v1");
    if (!i->srcMgr) missing << QStringLiteral("ext_foreign_toplevel_image_capture_source_manager_v1");
    if (!i->copyMgr) missing << QStringLiteral("ext_image_copy_capture_manager_v1");
    if (!missing.isEmpty()) {
        if (error) *error = QStringLiteral("compositor lacks ") + missing.join(QStringLiteral(", "));
        return false;
    }
    static const ext_foreign_toplevel_list_v1_listener ll = { Impl::lToplevel, Impl::lFinished };
    ext_foreign_toplevel_list_v1_add_listener(i->list, &ll, nullptr);
    wl_display_roundtrip(i->dpy);   // the current toplevels
    wl_display_roundtrip(i->dpy);   // their identifiers and `done`
    i->evfd = eventfd(0, EFD_CLOEXEC | EFD_NONBLOCK);
    m_available = true;
    i->publishKnown();
    m_thread = std::thread([this] { run(); });
    return true;
}

void CaptureWorker::setTargets(const QHash<QString, QSize> &t) {
    if (!m_available) return;
    {
        std::lock_guard<std::mutex> l(d->mu);
        d->targets = t;
        d->targetsDirty = true;
    }
    d->wake();
}

QSet<QString> CaptureWorker::knownIdentifiers() const {
    std::lock_guard<std::mutex> l(d->mu);
    return d->known;
}

QHash<QString, int> CaptureWorker::takeFrameCounts() {
    std::lock_guard<std::mutex> l(d->mu);
    QHash<QString, int> c;
    c.swap(d->counts);
    return c;
}

void CaptureWorker::run() {
    Impl *i = d.get();
    while (!i->quit) {
        while (wl_display_prepare_read(i->dpy) != 0) wl_display_dispatch_pending(i->dpy);
        wl_display_flush(i->dpy);
        pollfd fds[2] = { { wl_display_get_fd(i->dpy), POLLIN, 0 }, { i->evfd, POLLIN, 0 } };
        const int rc = ::poll(fds, 2, i->timeoutMs());
        if (rc > 0 && (fds[0].revents & POLLIN)) wl_display_read_events(i->dpy);
        else wl_display_cancel_read(i->dpy);
        if (rc > 0 && (fds[0].revents & (POLLERR | POLLHUP))) break;
        if (wl_display_dispatch_pending(i->dpy) < 0) break;
        if (rc > 0 && (fds[1].revents & POLLIN)) { uint64_t v; ssize_t r = ::read(i->evfd, &v, 8); (void)r; }
        i->applyTargets();
        i->tick();
    }
    m_available = false;
}
