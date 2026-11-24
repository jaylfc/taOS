#pragma once
#include <QHash>
#include <QObject>
#include <QSet>
#include <QTimer>
#include <QVariantList>
#include <functional>

#include "displayconfig.h"
#include "displaymodel.h"
#include "swayrunner.h"

// Everything the display feature does, in one object: keeps the output list,
// applies the native mode and the saved or recommended scale, changes scale
// and refresh on request, and runs the "Keep this setting?" trial for mode
// changes. It is the `Display` singleton of the bar and the engine of the
// `taos-display` tool. No GUI.
class DisplayController : public QObject {
    Q_OBJECT
    Q_PROPERTY(QVariantList outputs READ outputs NOTIFY outputsChanged)
    Q_PROPERTY(bool xwaylandPresent READ xwaylandPresent NOTIFY outputsChanged)
    Q_PROPERTY(bool anyScaled READ anyScaled NOTIFY outputsChanged)
    Q_PROPERTY(bool trialActive READ trialActive NOTIFY trialChanged)
    Q_PROPERTY(int trialSecondsLeft READ trialSecondsLeft NOTIFY trialChanged)
    Q_PROPERTY(QString trialLabel READ trialLabel NOTIFY trialChanged)
    Q_PROPERTY(QString trialOutput READ trialOutput NOTIFY trialChanged)
    Q_PROPERTY(QString trialText READ trialText NOTIFY trialChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
public:
    // The owner's number: how long "Keep this setting?" waits before it reverts.
    static constexpr int kConfirmSeconds = 10;

    struct ApplyOptions {
        // A mode change that has no saved setting behind it (first run of a monitor):
        enum Unsaved { Defer, Trial, Immediate } unsaved = Defer;
        // Defer: leave it (the CLI at session start; the bar does it with a dialog).
        // Trial: start the Keep this setting? dialog. Immediate: apply, verify, revert on failure, no dialog.
    };
    struct ApplyReport {
        QStringList changed;   // "HDMI-A-1: mode 3840x1200@120Hz", "HDMI-A-1: scale 1"
        QStringList deferred;  // changes left for the bar's dialog
        QStringList reverted;
        QStringList errors;
    };

    DisplayController(SwayRunner *sway, DisplayConfigFile cfg, QString drmRoot = QStringLiteral("/sys/class/drm"),
                      std::function<qint64()> nowMs = {}, QObject *parent = nullptr);

    // How long to wait between an applied mode and the check that it holds (0 in tests).
    void setSettleMs(int ms) { m_settleMs = ms; }
    void setSleeper(std::function<void(int)> s) { m_sleep = std::move(s); }

    QVariantList outputs() const { return m_model; }
    bool xwaylandPresent() const { return m_xwayland; }
    bool anyScaled() const;
    bool trialActive() const { return m_trial.active; }
    int trialSecondsLeft() const;
    QString trialLabel() const { return m_trial.label; }
    QString trialOutput() const { return m_trial.output; }
    QString trialText() const { return m_trial.active ? modeText(m_trial.target) : QString(); }
    QString errorText() const { return m_error; }
    const QList<DisplayOutput> &rawOutputs() const { return m_outputs; }

public slots:
    // Reads get_outputs and get_tree again and rebuilds the model.
    bool refresh();
    // Session start and hotplug: native mode and the saved or recommended scale for every output.
    ApplyReport applyAll() { return applyAll(ApplyOptions()); }
    ApplyReport applyAll(const ApplyOptions &o);
    // Live, no confirmation; saved right away.
    bool setScale(const QString &output, double scale);
    // Starts the Keep this setting? trial for that refresh rate at the native resolution.
    bool setRefresh(const QString &output, int mHz);
    // Native resolution, highest refresh, recommended scale.
    bool useRecommended(const QString &output);
    void confirm();
    void revert();
    // The trial's clock: call from a timer (the controller runs its own) or from a test.
    void poll();
    void clearError();

signals:
    void outputsChanged();
    void trialChanged();
    void errorChanged();
    // Spoken to assistive tech: "Scale 125 percent".
    void announcement(const QString &text);

private:
    struct Trial {
        bool active = false;
        QString output, key, label;
        DisplayMode previous, target;
        qint64 deadline = 0;
    };
    const DisplayOutput *find(const QString &name) const;
    bool setModeChecked(const QString &name, const DisplayMode &target, const DisplayMode &previous);
    bool startTrial(const DisplayOutput &o, const DisplayMode &target);
    void endTrial();
    void rebuildModel();
    void fail(const QString &text);
    bool readOutputs(QList<DisplayOutput> *out);
    void finishTrialRetry();

    SwayRunner *m_sway;
    DisplayConfigFile m_cfg;
    QString m_drmRoot;
    std::function<qint64()> m_now;
    std::function<void(int)> m_sleep;
    int m_settleMs = 1200;
    QList<DisplayOutput> m_outputs;
    QVariantList m_model;
    bool m_xwayland = false;
    Trial m_trial;
    QHash<QString, QList<DisplayMode>> m_rejected;  // by monitor key, this session
    QSet<QString> m_declined;                       // monitors whose automatic change the person turned down
    QString m_error;
    QTimer m_timer;
};
