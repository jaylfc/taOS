// taos-display: native resolution, best refresh and the saved scale for every
// output. Run `taos-display apply` at session start (sway exec_always); the bar
// does the same on monitor hotplug and owns the "Keep this setting?" dialog.
//
//   taos-display apply [--now]   apply the saved settings; --now also applies a
//                                first-run mode change at once (verified, undone
//                                if it does not hold) instead of leaving it to the bar
//   taos-display status          what each output has, offers and would get
//   taos-display scale OUTPUT PERCENT
//   taos-display watch           apply on every monitor event (when there is no bar)
#include <QCoreApplication>
#include <QTextStream>

#include "displaycontroller.h"
#include "outputwatcher.h"

static int usage() {
    fprintf(stderr, "usage: taos-display apply [--now] | status | scale OUTPUT PERCENT | watch\n");
    return 2;
}

static int printReport(const DisplayController::ApplyReport &r) {
    QTextStream out(stdout);
    for (const QString &s : r.changed) out << "changed: " << s << "\n";
    for (const QString &s : r.deferred) out << "left for the bar: " << s << "\n";
    for (const QString &s : r.reverted) out << "went back: " << s << "\n";
    for (const QString &s : r.errors) fprintf(stderr, "taos-display: %s\n", qPrintable(s));
    return r.errors.isEmpty() ? 0 : 1;
}

int main(int argc, char **argv) {
    QCoreApplication app(argc, argv);
    const QStringList a = app.arguments();
    if (a.size() < 2) return usage();
    SwayMsg sway;
    DisplayController dc(&sway, DisplayConfigFile());
    const QString cmd = a.at(1);
    if (cmd == QLatin1String("apply")) {
        DisplayController::ApplyOptions o;
        if (a.contains(QStringLiteral("--now"))) o.unsaved = DisplayController::ApplyOptions::Immediate;
        return printReport(dc.applyAll(o));
    }
    if (cmd == QLatin1String("status")) {
        if (!dc.refresh()) { fprintf(stderr, "taos-display: %s\n", qPrintable(dc.errorText())); return 1; }
        QTextStream out(stdout);
        for (const DisplayOutput &o : dc.rawOutputs()) {
            const NativeChoice nc = chooseNative(o);
            out << o.name << "  " << o.key() << "\n  now: " << (o.current.valid() ? modeText(o.current) : QStringLiteral("?"))
                << ", scale " << scalePercent(o.scale) << "%\n  native: "
                << (nc.mode.valid() ? modeText(nc.mode) : QStringLiteral("unknown"))
                << (nc.source == NativeChoice::Flag ? " (flagged preferred)" : nc.source == NativeChoice::Edid ? " (EDID preferred)" : nc.source == NativeChoice::Current ? " (current resolution kept: no preferred mode known)" : "")
                << "\n  recommended scale: " << scalePercent(recommendedScale(o, nc.resolution.isValid() ? nc.resolution : QSize(o.current.width, o.current.height))) << "%\n";
        }
        return 0;
    }
    if (cmd == QLatin1String("scale") && a.size() == 4) {
        if (!dc.refresh()) { fprintf(stderr, "taos-display: %s\n", qPrintable(dc.errorText())); return 1; }
        bool ok = false;
        const int pct = a.at(3).toInt(&ok);
        if (!ok || pct < 50 || pct > 400) return usage();
        if (!dc.setScale(a.at(2), pct / 100.0)) { fprintf(stderr, "taos-display: %s\n", qPrintable(dc.errorText())); return 1; }
        return 0;
    }
    if (cmd == QLatin1String("watch")) {
        OutputWatcher w;
        DisplayController::ApplyOptions o;
        o.unsaved = DisplayController::ApplyOptions::Immediate;
        QObject::connect(&w, &OutputWatcher::outputsChanged, &app, [&] { printReport(dc.applyAll(o)); });
        w.start();
        printReport(dc.applyAll(o));
        return app.exec();
    }
    return usage();
}
