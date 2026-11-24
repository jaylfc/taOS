// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// Pure formatting and normalising helpers of the Activity app. They port the
// web app's rules (ActivityApp.tsx, lib/model-activity-api.ts, lib/cluster.ts)
// and hold no state, so they are unit-tested without a controller.
#include <QJsonObject>
#include <QObject>
#include <QVariantList>
#include <QVariantMap>

namespace Taos::Activity {

class Fmt : public QObject
{
    Q_OBJECT
public:
    explicit Fmt(QObject *parent = nullptr) : QObject(parent) {}

    /// "12 B/s", "3.4 KB/s", "1.2 MB/s", "1.25 GB/s" (formatBytes).
    Q_INVOKABLE static QString rate(double bps);
    /// "1.5 GB", "512 MB", or an em dash for a negative (unknown) value (formatMb).
    Q_INVOKABLE static QString mb(double mb);
    /// "12 ms", "3.4 s", "2m 5s" (formatDuration); empty for a negative value.
    Q_INVOKABLE static QString duration(double ms);
    /// "42 tok · 12.5 tok/s" (formatTokens); empty when there is no token count.
    Q_INVOKABLE static QString tokens(const QVariantMap &event);
    /// "3s ago", "2m ago", "1.5h ago", "never" (formatRelativeSeconds).
    Q_INVOKABLE static QString relative(double ts, double nowSec);
    /// "Loaded", "Unloaded", ... or the raw event name.
    Q_INVOKABLE static QString eventLabel(const QString &event);
    /// 0 neutral, 1 ok, 2 warn, 3 critical, 4 info; the tone of an event badge.
    Q_INVOKABLE static int eventTone(const QString &event);
    /// "brain", "vector", "image", "audio", "vision" or "code" (modelIconKind).
    Q_INVOKABLE static QString modelKind(const QString &model);
    /// "Intel Core i5-10600" from a raw CPU model string (shortCpuName).
    Q_INVOKABLE static QString shortCpuName(const QString &model);
    /// The hardware label for "cpu", "gpu" or "npu" from the activity `hardware` block.
    Q_INVOKABLE static QString hardwareLabel(const QString &kind, const QVariantMap &hardware);
    /// The "Board . arch . N GB RAM" line under the title.
    Q_INVOKABLE static QString systemLine(const QVariantMap &hardware);
    /// "C0 2.4G" style core label.
    Q_INVOKABLE static QString coreLabel(const QVariantMap &core);
    /// `name@http://localhost:11434` becomes `name:11434`.
    Q_INVOKABLE static QString backendName(const QString &name);
    /// 0 online, 1 stale, 2 offline, 3 unknown from a heartbeat (epoch s).
    Q_INVOKABLE static int heartbeatState(double lastHeartbeat, double nowSec);
    Q_INVOKABLE static QString stateLabel(int state);
    /// Percent 0..100 clamped, as text ("42%").
    Q_INVOKABLE static QString percent(double v);

    /// The GPUs the controller reports, as a list (one object becomes a list
    /// of one). See ActivityEngine docs: `gpus[]`, `gpu.devices[]` or the
    /// single `gpu` block.
    static QVariantList gpus(const QVariantMap &activity);
    /// Loaded models: local rows (host defaults to "controller") plus every
    /// worker backend's `loaded_models` (strings or objects).
    static QVariantList mergeLoaded(const QVariantList &local, const QVariantList &workers);
};

} // namespace Taos::Activity
