#pragma once
#include <QColor>
#include <QObject>
#include <QString>
#include <QStringList>

// Theme: colours and motion preference for the bar, taken from a theme
// package (`<dir>/<id>/theme.json`, dark and light variants). A theme file
// that has no "colors" section (an older package) is completed key by key
// from the built-in copy of the default theme.
class Theme : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString id READ id CONSTANT)
    Q_PROPERTY(QString variant READ variant CONSTANT)
    Q_PROPERTY(QString source READ source CONSTANT)
    Q_PROPERTY(QColor ground READ ground CONSTANT)
    Q_PROPERTY(QColor barBackground READ barBackground CONSTANT)
    Q_PROPERTY(QColor stroke READ stroke CONSTANT)
    Q_PROPERTY(QColor text READ text CONSTANT)
    Q_PROPERTY(QColor textSecondary READ textSecondary CONSTANT)
    Q_PROPERTY(QColor textTertiary READ textTertiary CONSTANT)
    Q_PROPERTY(QColor accent READ accent CONSTANT)
    Q_PROPERTY(QColor warning READ warning CONSTANT)
    Q_PROPERTY(QColor danger READ danger CONSTANT)
    Q_PROPERTY(QColor hover READ hover CONSTANT)
    Q_PROPERTY(QColor pressed READ pressed CONSTANT)
    Q_PROPERTY(bool reduceMotion READ reduceMotion CONSTANT)
public:
    static constexpr double kBarOpacity = 0.72;

    explicit Theme(QObject *parent = nullptr) : QObject(parent) {}

    // searchDirs: directories that hold <id>/theme.json, first match wins.
    // builtinPath: path (or qrc path) of the shipped default theme.json.
    // Returns false (and fills `error`) only when no usable colours exist at all.
    bool load(const QString &id, const QString &variant, const QStringList &searchDirs,
              const QString &builtinPath, bool reduceMotionFlag, QString *error = nullptr);

    QString id() const { return m_id; }
    QString variant() const { return m_variant; }
    QString source() const { return m_source; }
    QColor ground() const { return m_ground; }
    QColor barBackground() const { return m_bar; }
    QColor stroke() const { return m_stroke; }
    QColor text() const { return m_text; }
    QColor textSecondary() const { return m_text2; }
    QColor textTertiary() const { return m_text3; }
    QColor accent() const { return m_accent; }
    QColor warning() const { return m_warning; }
    QColor danger() const { return m_danger; }
    QColor hover() const { return m_hover; }
    QColor pressed() const { return m_pressed; }
    bool reduceMotion() const { return m_reduce; }

private:
    QString m_id, m_variant, m_source;
    QColor m_ground, m_bar, m_stroke, m_text, m_text2, m_text3, m_accent, m_warning, m_danger;
    QColor m_hover, m_pressed;
    bool m_reduce = false;
};
