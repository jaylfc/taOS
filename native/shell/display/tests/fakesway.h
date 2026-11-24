// SPDX-License-Identifier: AGPL-3.0-or-later
#pragma once
// A scripted compositor for the display tests: serves get_outputs and get_tree
// from fixture JSON, applies `output NAME mode|scale ...` to its own state, and
// can be told to refuse a mode, to accept it without effect, or to go black.
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QRegularExpression>
#include <QSet>

#include "swayrunner.h"

class FakeSway : public SwayRunner {
public:
    QJsonArray outs;
    QByteArray tree = "{\"type\":\"root\",\"nodes\":[]}";
    QStringList log;                 // every `output ...` command, in order
    QSet<QString> refuse;            // "3840x1200@120Hz": the command fails
    QSet<QString> ignore;            // accepted but nothing changes
    QSet<QString> blank;             // accepted, applied, then the output reads back inactive
    bool failReads = false;

    bool load(const QString &file) {
        QFile f(file);
        if (!f.open(QIODevice::ReadOnly)) return false;
        outs = QJsonDocument::fromJson(f.readAll()).array();
        return !outs.isEmpty();
    }
    QJsonObject out(const QString &name) const {
        for (const QJsonValue &v : outs) if (v.toObject().value("name").toString() == name) return v.toObject();
        return {};
    }
    void put(const QString &name, const QJsonObject &o) {
        for (int i = 0; i < outs.size(); ++i) if (outs[i].toObject().value("name").toString() == name) outs[i] = o;
    }
    int count(const QString &needle) const {
        int n = 0;
        for (const QString &l : log) if (l.contains(needle)) ++n;
        return n;
    }
    Result run(const QStringList &a) override {
        Result r;
        if (a.size() >= 2 && a[0] == "-t") {
            if (failReads) { r.error = "no socket"; return r; }
            r.ok = true;
            r.out = a[1] == "get_outputs" ? QJsonDocument(outs).toJson(QJsonDocument::Compact) : tree;
            return r;
        }
        if (a.size() >= 4 && a[0] == "output") {
            log << a.join(' ');
            QJsonObject o = out(a[1]);
            if (o.isEmpty()) { r.error = "Unknown output " + a[1]; return r; }
            if (a[2] == "mode") {
                if (refuse.contains(a[3])) { r.error = "Cannot apply mode " + a[3]; return r; }
                const auto m = QRegularExpression("^(\\d+)x(\\d+)@([0-9.]+)Hz$").match(a[3]);
                if (!m.hasMatch()) { r.error = "bad mode"; return r; }
                if (!ignore.contains(a[3])) {
                    QJsonObject cm;
                    cm["width"] = m.captured(1).toInt(); cm["height"] = m.captured(2).toInt();
                    cm["refresh"] = qRound(m.captured(3).toDouble() * 1000);
                    o["current_mode"] = cm;
                    o["active"] = !blank.contains(a[3]);
                    put(a[1], o);
                }
            } else if (a[2] == "scale") {
                o["scale"] = a[3].toDouble();
                put(a[1], o);
            }
            r.ok = true;
            return r;
        }
        r.error = "unsupported";
        return r;
    }
};
