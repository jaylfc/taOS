#pragma once
#include <QObject>
#include <QString>

// Launcher: stand-in for the real launcher. Runs a configurable command as a
// detached process.
class Launcher : public QObject {
    Q_OBJECT
    Q_PROPERTY(QString command READ command WRITE setCommand NOTIFY commandChanged)
public:
    explicit Launcher(QObject *parent = nullptr) : QObject(parent) {}
    QString command() const { return m_cmd; }
    void setCommand(const QString &c);

    // Returns false when the command is empty or cannot be started.
    Q_INVOKABLE bool launch();

signals:
    void commandChanged();

private:
    QString m_cmd = QStringLiteral("foot");
};
