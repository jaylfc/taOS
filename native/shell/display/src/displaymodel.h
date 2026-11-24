// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
#include <QByteArray>
#include <QList>
#include <QSize>
#include <QSizeF>
#include <QString>
#include <QStringList>

// Pure logic of the display feature: the data sway reports, which mode counts
// as the panel's native one, and the scale to recommend. No I/O except the
// optional sysfs readers at the bottom, which take their root as a parameter.

struct DisplayMode {
    int width = 0;
    int height = 0;
    int mHz = 0;            // refresh in millihertz, as sway reports it (120000 = 120 Hz)
    bool preferred = false;  // only if the compositor flagged it (sway 1.12 does not)
    bool valid() const { return width > 0 && height > 0 && mHz > 0; }
    bool sameSize(const DisplayMode &o) const { return width == o.width && height == o.height; }
    bool operator==(const DisplayMode &o) const { return width == o.width && height == o.height && mHz == o.mHz; }
};

struct DisplayOutput {
    QString name;    // connector, "HDMI-A-1"
    QString make, model, serial;
    bool active = false;
    double scale = 1.0;
    DisplayMode current;
    QList<DisplayMode> modes;
    // Not in sway's JSON; filled from sysfs (readDrmHints) or by tests.
    QSize drmPreferred;      // first line of the connector's `modes` file
    QSizeF physicalMm;       // from the EDID, 0 when unknown

    // The identifier sway matches `output "<id>"` against: "make model serial".
    QString key() const;
    // What people call it: the model; the connector when the model is unknown.
    QString label() const;
};

// "3840 x 1200, 120 Hz" style pieces.
QString hzText(int mHz);                                  // "120" or "59.94"
QString modeText(const DisplayMode &m);                   // "3840 x 1200, 120 Hz"
QString swayModeArg(int w, int h, int mHz);               // "3840x1200@120Hz" (mHz precision)

// get_outputs -> outputs (inactive ones are skipped). Returns false on bad JSON.
bool parseOutputs(const QByteArray &json, QList<DisplayOutput> *out, QString *error = nullptr);
// get_tree: true if any window is an XWayland one.
bool treeHasXwayland(const QByteArray &json);

struct NativeChoice {
    enum Source { None, Flag, Edid, Current };
    DisplayMode mode;     // invalid when source is None
    Source source = None;
    QSize resolution;     // the native resolution (also when mode is invalid)
};
// The native mode: the preferred resolution (the compositor's flag if there is
// one, else the EDID's first listed mode, else the current resolution) at the
// highest refresh available AT THAT RESOLUTION. Never a larger resolution that
// is not the preferred one. `rejected` modes (failed or reverted) are skipped.
NativeChoice chooseNative(const DisplayOutput &o, const QList<DisplayMode> &rejected = {});
// Refresh rates (mHz, descending, distinct) offered at a resolution.
QList<int> ratesAt(const DisplayOutput &o, const QSize &res);

// Presets, as fractions: 1.0 .. 2.0 in steps of 0.25.
QList<double> scalePresets();
// Scale for a panel of this physical size: aim for about 125 logical dpi
// (inside 110 to 140), rounded to the nearest preset, never below 100%.
// Unknown or implausible physical size gives 1.0.
double recommendedScale(int pxWidth, int pxHeight, double mmWidth, double mmHeight);
double recommendedScale(const DisplayOutput &o, const QSize &res);
// 125 for 1.25, rounding to the nearest percent.
int scalePercent(double scale);

// sysfs / EDID.
struct DrmHints { QSize preferred; QSizeF physicalMm; };
QSizeF edidPhysicalMm(const QByteArray &edid);
DrmHints readDrmHints(const QString &connector, const QString &sysRoot = QStringLiteral("/sys/class/drm"));
