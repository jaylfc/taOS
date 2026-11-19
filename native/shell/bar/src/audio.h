#pragma once
#include <QAbstractListModel>
#include <QObject>

#include "audiobackend.h"

class ChoiceModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { IdRole = Qt::UserRole + 1, TitleRole, SubtitleRole, IconRole, ActiveRole, DescriptionRole };
    explicit ChoiceModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setRows(const QList<AudioChoice> &rows);
    const QList<AudioChoice> &rows() const { return m_rows; }
private:
    QList<AudioChoice> m_rows;
};

class ProfileModel : public QAbstractListModel {
    Q_OBJECT
public:
    enum Roles { CardRole = Qt::UserRole + 1, NameRole, LabelRole, ActiveRole };
    explicit ProfileModel(QObject *parent = nullptr) : QAbstractListModel(parent) {}
    int rowCount(const QModelIndex &p = {}) const override { return p.isValid() ? 0 : int(m_rows.size()); }
    QVariant data(const QModelIndex &i, int role) const override;
    QHash<int, QByteArray> roleNames() const override;
    void setRows(const QList<ProfileChoice> &rows);
    const QList<ProfileChoice> &rows() const { return m_rows; }
private:
    QList<ProfileChoice> m_rows;
};

// The audio face of the bar: the volume icon, the audio menu and the keyboard
// volume keys all talk to this. Everything comes from the backend's events.
class Audio : public QObject {
    Q_OBJECT
    Q_PROPERTY(bool available READ available NOTIFY changed)
    Q_PROPERTY(bool hasOutput READ hasOutput NOTIFY changed)
    Q_PROPERTY(bool hasInput READ hasInput NOTIFY changed)
    Q_PROPERTY(int percent READ percent NOTIFY changed)
    Q_PROPERTY(bool muted READ muted NOTIFY changed)
    Q_PROPERTY(int inputPercent READ inputPercent NOTIFY changed)
    Q_PROPERTY(bool inputMuted READ inputMuted NOTIFY changed)
    Q_PROPERTY(QString level READ level NOTIFY changed)  // none, muted, low, medium, high
    Q_PROPERTY(QString description READ description NOTIFY changed)
    Q_PROPERTY(QString message READ message NOTIFY changed)  // non-empty: shown instead of the controls
    Q_PROPERTY(ChoiceModel *outputs READ outputs CONSTANT)
    Q_PROPERTY(ChoiceModel *inputs READ inputs CONSTANT)
    Q_PROPERTY(ProfileModel *outputProfiles READ outputProfiles CONSTANT)
    Q_PROPERTY(ProfileModel *inputProfiles READ inputProfiles CONSTANT)
    Q_PROPERTY(QString outputProfileTitle READ outputProfileTitle NOTIFY changed)
    Q_PROPERTY(QString inputProfileTitle READ inputProfileTitle NOTIFY changed)
    Q_PROPERTY(double micLevel READ micLevel NOTIFY micLevelChanged)
    Q_PROPERTY(QString errorText READ errorText NOTIFY errorChanged)
    Q_PROPERTY(bool menuOpen READ menuOpen NOTIFY menuOpenChanged)
public:
    explicit Audio(AudioBackend *backend, QObject *parent = nullptr);
    bool available() const { return m_b->state().connected; }
    bool hasOutput() const { return m_b->state().defaultSinkDevice() != nullptr; }
    bool hasInput() const;
    int percent() const;
    bool muted() const;
    int inputPercent() const;
    bool inputMuted() const;
    QString level() const;
    QString description() const;
    QString message() const;
    ChoiceModel *outputs() { return &m_outputs; }
    ChoiceModel *inputs() { return &m_inputs; }
    ProfileModel *outputProfiles() { return &m_outProfiles; }
    ProfileModel *inputProfiles() { return &m_inProfiles; }
    QString outputProfileTitle() const { return m_outProfileTitle; }
    QString inputProfileTitle() const { return m_inProfileTitle; }
    double micLevel() const { return m_mic; }
    QString errorText() const { return m_error; }
    bool menuOpen() const { return m_menuOpen; }

    Q_INVOKABLE void setPercent(int p);
    Q_INVOKABLE void setMuted(bool m);
    Q_INVOKABLE void setInputPercent(int p);
    Q_INVOKABLE void setInputMuted(bool m);
    Q_INVOKABLE void selectOutput(const QString &id);
    Q_INVOKABLE void selectInput(const QString &id);
    Q_INVOKABLE void selectProfile(quint32 card, const QString &profile, bool output);
    // Menu open: the live microphone meter runs. Closed: it does not.
    Q_INVOKABLE void setMenuOpen(bool open);
    Q_INVOKABLE void clearError();

    // Keyboard keys (taos-bar volume ...): 5 point steps, never above 100.
    static constexpr int kStep = 5;
    void stepVolume(int delta);
    void toggleMute();
    void toggleMicMute();

signals:
    void changed();
    void micLevelChanged();
    void errorChanged();
    void menuOpenChanged();
    void announcement(const QString &text);
    // On-screen flash for the keys: kind is "volume" or "mic"; percent -1 = no device.
    void flash(const QString &kind, int percent, bool muted);

private:
    void onState();
    void startMeterIfNeeded();
    void applyPending();
    void setError(const QString &t);
    AudioBackend *m_b;
    ChoiceModel m_outputs, m_inputs;
    ProfileModel m_outProfiles, m_inProfiles;
    QString m_outProfileTitle, m_inProfileTitle;
    double m_mic = 0;
    bool m_menuOpen = false;
    QString m_error;
    QString m_meterSource;
    struct Pending { bool active = false, output = true; quint32 card = kNoIndex; QString profile; } m_pending;
    // What was last announced, so that a state event that changes nothing is silent.
    QString m_lastDefaultSink, m_lastDefaultSource;
};
