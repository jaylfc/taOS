#include "audio.h"

#include <algorithm>

// ---- models --------------------------------------------------------------

QHash<int, QByteArray> ChoiceModel::roleNames() const {
    return {{IdRole, "id"}, {TitleRole, "title"}, {SubtitleRole, "subtitle"}, {IconRole, "icon"}, {ActiveRole, "active"}, {DescriptionRole, "description"}};
}
QVariant ChoiceModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() < 0 || i.row() >= m_rows.size()) return {};
    const AudioChoice &c = m_rows.at(i.row());
    switch (role) {
    case IdRole: return c.id;
    case TitleRole: case Qt::DisplayRole: return c.title;
    case SubtitleRole: return c.subtitle;
    case IconRole: return c.icon;
    case ActiveRole: return c.active;
    case DescriptionRole:
        return c.subtitle.isEmpty() ? (c.active ? tr("%1, selected").arg(c.title) : c.title)
                                    : (c.active ? tr("%1, %2, selected").arg(c.title, c.subtitle) : tr("%1, %2").arg(c.title, c.subtitle));
    }
    return {};
}
void ChoiceModel::setRows(const QList<AudioChoice> &rows) {
    bool same = rows.size() == m_rows.size();
    for (int i = 0; same && i < rows.size(); ++i) same = rows.at(i).id == m_rows.at(i).id;
    if (!same) {
        beginResetModel();
        m_rows = rows;
        endResetModel();
        return;
    }
    for (int i = 0; i < rows.size(); ++i) {
        const AudioChoice &a = m_rows.at(i), &b = rows.at(i);
        if (a.active != b.active || a.title != b.title || a.subtitle != b.subtitle) {
            m_rows[i] = b;
            emit dataChanged(index(i), index(i));
        } else {
            m_rows[i] = b;  // ids equal; refresh the rest silently
        }
    }
}

QHash<int, QByteArray> ProfileModel::roleNames() const {
    return {{CardRole, "card"}, {NameRole, "name"}, {LabelRole, "label"}, {ActiveRole, "active"}};
}
QVariant ProfileModel::data(const QModelIndex &i, int role) const {
    if (!i.isValid() || i.row() < 0 || i.row() >= m_rows.size()) return {};
    const ProfileChoice &c = m_rows.at(i.row());
    switch (role) {
    case CardRole: return c.card;
    case NameRole: return c.name;
    case LabelRole: case Qt::DisplayRole: return c.label;
    case ActiveRole: return c.active;
    }
    return {};
}
void ProfileModel::setRows(const QList<ProfileChoice> &rows) {
    bool same = rows.size() == m_rows.size();
    for (int i = 0; same && i < rows.size(); ++i) same = rows.at(i).name == m_rows.at(i).name && rows.at(i).card == m_rows.at(i).card;
    if (!same) {
        beginResetModel();
        m_rows = rows;
        endResetModel();
        return;
    }
    for (int i = 0; i < rows.size(); ++i)
        if (m_rows.at(i).active != rows.at(i).active) {
            m_rows[i] = rows.at(i);
            emit dataChanged(index(i), index(i));
        }
}

// ---- controller ----------------------------------------------------------

Audio::Audio(AudioBackend *backend, QObject *parent) : QObject(parent), m_b(backend) {
    connect(m_b, &AudioBackend::stateChanged, this, &Audio::onState);
    connect(m_b, &AudioBackend::failed, this, [this](const QString &w) { setError(tr("Sound did not change (%1).").arg(w)); });
    connect(m_b, &AudioBackend::meterLevel, this, [this](double l) {
        if (!m_menuOpen) return;
        m_mic = l;
        emit micLevelChanged();
    });
    onState();
}

bool Audio::hasInput() const {
    const AudioDevice *d = m_b->state().defaultSourceDevice();
    return d && !d->monitor;
}
int Audio::percent() const { const auto *d = m_b->state().defaultSinkDevice(); return d ? d->volume : 0; }
bool Audio::muted() const { const auto *d = m_b->state().defaultSinkDevice(); return d && d->mute; }
int Audio::inputPercent() const { const auto *d = m_b->state().defaultSourceDevice(); return d ? d->volume : 0; }
bool Audio::inputMuted() const { const auto *d = m_b->state().defaultSourceDevice(); return d && d->mute; }

QString Audio::level() const {
    if (!hasOutput()) return QStringLiteral("none");
    if (muted() || percent() == 0) return QStringLiteral("muted");
    const int p = percent();
    return p < 34 ? QStringLiteral("low") : p < 67 ? QStringLiteral("medium") : QStringLiteral("high");
}

QString Audio::description() const {
    if (!available()) return tr("Sound is not available");
    if (!hasOutput()) return tr("No sound output");
    return muted() ? tr("Volume muted") : tr("Volume %1 percent").arg(percent());
}

QString Audio::message() const {
    if (!available()) return tr("Sound is not available on this computer");
    return {};
}

void Audio::setError(const QString &t) {
    m_error = t;
    emit errorChanged();
    if (!t.isEmpty()) emit announcement(t);
}
void Audio::clearError() { if (!m_error.isEmpty()) { m_error.clear(); emit errorChanged(); } }

void Audio::onState() {
    const AudioState &s = m_b->state();
    m_outputs.setRows(AudioNames::choices(s, true));
    m_inputs.setRows(AudioNames::choices(s, false));
    m_outProfiles.setRows(AudioNames::profileChoices(s, true, &m_outProfileTitle));
    m_inProfiles.setRows(AudioNames::profileChoices(s, false, &m_inProfileTitle));
    if (m_outProfiles.rowCount() == 0) m_outProfileTitle.clear();
    if (m_inProfiles.rowCount() == 0) m_inProfileTitle.clear();
    // A choice that needed a profile switch: finish it when the new device exists.
    applyPending();
    // Say it when the default moves (plugging a headset, unplugging a cable).
    if (s.connected && !m_lastDefaultSink.isEmpty() && s.defaultSink != m_lastDefaultSink && s.defaultSinkDevice()) {
        for (const AudioChoice &c : m_outputs.rows())
            if (c.active) { emit announcement(tr("Sound output: %1").arg(c.title)); break; }
    }
    m_lastDefaultSink = s.defaultSink;
    m_lastDefaultSource = s.defaultSource;
    startMeterIfNeeded();
    emit changed();
}

void Audio::applyPending() {
    if (!m_pending.active) return;
    const AudioState &s = m_b->state();
    const AudioCard *c = s.card(m_pending.card);
    if (!c || c->activeProfile != m_pending.profile) return;
    for (const AudioDevice &d : m_pending.output ? s.sinks : s.sources) {
        if (d.card != m_pending.card || d.monitor) continue;
        const bool out = m_pending.output;
        m_pending = Pending();
        m_b->setDefault(out, d.name);
        return;
    }
}

void Audio::setPercent(int p) {
    const auto *d = m_b->state().defaultSinkDevice();
    if (d) m_b->setVolume(true, d->index, std::max(0, std::min(100, p)));
}
void Audio::setMuted(bool m) {
    const auto *d = m_b->state().defaultSinkDevice();
    if (d) { m_b->setMute(true, d->index, m); emit announcement(m ? tr("Sound muted") : tr("Sound unmuted")); }
}
void Audio::setInputPercent(int p) {
    const auto *d = m_b->state().defaultSourceDevice();
    if (d) m_b->setVolume(false, d->index, std::max(0, std::min(100, p)));
}
void Audio::setInputMuted(bool m) {
    const auto *d = m_b->state().defaultSourceDevice();
    if (d) { m_b->setMute(false, d->index, m); emit announcement(m ? tr("Microphone muted") : tr("Microphone unmuted")); }
}

void Audio::selectOutput(const QString &id) {
    clearError();
    for (const AudioChoice &c : m_outputs.rows()) {
        if (c.id != id) continue;
        if (!c.profileName.isEmpty()) {
            m_pending = {true, true, c.card, c.profileName};
            m_b->setCardProfile(c.card, c.profileName);
        } else {
            const AudioDevice *cur = nullptr;
            for (const AudioDevice &d : m_b->state().sinks) if (d.name == c.deviceName) cur = &d;
            if (cur && !c.portName.isEmpty() && cur->activePort != c.portName) m_b->setPort(true, cur->index, c.portName);
            m_b->setDefault(true, c.deviceName);
        }
        emit announcement(tr("Sound output: %1").arg(c.title));
        return;
    }
}

void Audio::selectInput(const QString &id) {
    clearError();
    for (const AudioChoice &c : m_inputs.rows()) {
        if (c.id != id) continue;
        if (!c.profileName.isEmpty()) {
            m_pending = {true, false, c.card, c.profileName};
            m_b->setCardProfile(c.card, c.profileName);
        } else {
            const AudioDevice *cur = nullptr;
            for (const AudioDevice &d : m_b->state().sources) if (d.name == c.deviceName) cur = &d;
            if (cur && !c.portName.isEmpty() && cur->activePort != c.portName) m_b->setPort(false, cur->index, c.portName);
            m_b->setDefault(false, c.deviceName);
        }
        emit announcement(tr("Microphone: %1").arg(c.title));
        return;
    }
}

void Audio::selectProfile(quint32 card, const QString &profile, bool output) {
    clearError();
    // After a profile switch the default may need to follow (Bluetooth: the headset appears as a new device).
    m_pending = {true, output, card, profile};
    m_b->setCardProfile(card, profile);
    const ProfileModel &m = output ? m_outProfiles : m_inProfiles;
    for (const ProfileChoice &p : m.rows())
        if (p.name == profile) emit announcement(tr("Profile: %1").arg(p.label));
}

void Audio::setMenuOpen(bool open) {
    if (m_menuOpen == open) return;
    m_menuOpen = open;
    if (open) startMeterIfNeeded();
    else {
        m_b->stopMeter();
        m_meterSource.clear();
        m_mic = 0;
    }
    emit menuOpenChanged();
}

void Audio::startMeterIfNeeded() {
    if (!m_menuOpen) return;
    const AudioDevice *src = m_b->state().defaultSourceDevice();
    const QString want = src && !src->monitor ? src->name : QString();
    if (want == m_meterSource) return;
    m_meterSource = want;
    m_b->stopMeter();
    m_mic = 0;
    if (!want.isEmpty()) m_b->startMeter(want);
    emit micLevelChanged();
}

void Audio::stepVolume(int delta) {
    const AudioDevice *d = m_b->state().defaultSinkDevice();
    if (!d) { emit flash(QStringLiteral("volume"), -1, false); return; }
    int target = d->volume;
    if (delta > 0 && target < 100) target = std::min(100, target + delta);  // capped: no over-amplification by keys
    else if (delta < 0) target = std::max(0, target + delta);
    bool mute = d->mute;
    if (delta > 0 && mute) mute = false;
    if (target != d->volume) m_b->setVolume(true, d->index, target);
    if (mute != d->mute) m_b->setMute(true, d->index, mute);
    emit flash(QStringLiteral("volume"), target, mute);
}

void Audio::toggleMute() {
    const AudioDevice *d = m_b->state().defaultSinkDevice();
    if (!d) { emit flash(QStringLiteral("volume"), -1, false); return; }
    m_b->setMute(true, d->index, !d->mute);
    emit flash(QStringLiteral("volume"), d->volume, !d->mute);
}

void Audio::toggleMicMute() {
    const AudioDevice *d = m_b->state().defaultSourceDevice();
    if (!d || d->monitor) { emit flash(QStringLiteral("mic"), -1, false); return; }
    m_b->setMute(false, d->index, !d->mute);
    emit flash(QStringLiteral("mic"), d->volume, !d->mute);
}
