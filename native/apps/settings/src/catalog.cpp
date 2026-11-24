// SPDX-License-Identifier: AGPL-3.0-or-later
#include "catalog.h"

#include <algorithm>

#include <QVariantMap>

using namespace Qt::StringLiterals;

namespace TaosSettings {

void Catalog::addSection(const SectionEntry &s)
{
    m_sections.append(s);
    emit changed();
}

void Catalog::addSetting(const SettingEntry &s)
{
    m_settings.append(s);
}

QVariantList Catalog::sections() const
{
    QVariantList out;
    for (const SectionEntry &s : m_sections)
        out << QVariantMap{{u"id"_s, s.id}, {u"title"_s, s.title}, {u"icon"_s, s.icon}, {u"component"_s, s.component}};
    return out;
}

bool Catalog::hasSection(const QString &id) const
{
    return std::any_of(m_sections.begin(), m_sections.end(), [&](const SectionEntry &s) { return s.id == id; });
}

QString Catalog::sectionTitle(const QString &id) const
{
    for (const SectionEntry &s : m_sections)
        if (s.id == id)
            return s.title;
    return {};
}

QString Catalog::component(const QString &id) const
{
    for (const SectionEntry &s : m_sections)
        if (s.id == id)
            return s.component;
    return {};
}

QString Catalog::sectionOf(const QString &settingId) const
{
    for (const SettingEntry &s : m_settings)
        if (s.id == settingId)
            return s.section;
    return {};
}

QString Catalog::title(const QString &settingId) const
{
    for (const SettingEntry &s : m_settings)
        if (s.id == settingId)
            return s.title;
    return {};
}

QString Catalog::description(const QString &settingId) const
{
    for (const SettingEntry &s : m_settings)
        if (s.id == settingId)
            return s.description;
    return {};
}

QVariantList Catalog::search(const QString &query) const
{
    const QStringList words = query.toLower().simplified().split(u' ', Qt::SkipEmptyParts);
    if (words.isEmpty())
        return {};
    struct Hit { int score; int order; QVariantMap row; };
    QList<Hit> hits;
    auto score = [&](const QString &title, const QString &rest) -> int {
        const QString t = title.toLower();
        const QString r = rest.toLower();
        int total = 0;
        for (const QString &w : words) {
            if (t.startsWith(w) || t.contains(u' ' + w))
                total += 100;
            else if (t.contains(w))
                total += 60;
            else if (r.contains(w))
                total += 20;
            else
                return -1;
        }
        return total;
    };
    int order = 0;
    for (const SectionEntry &s : m_sections) {
        const int sc = score(s.title, s.keywords.join(u' '));
        if (sc >= 0)
            hits.append({sc - 1, order, QVariantMap{{u"kind"_s, u"section"_s}, {u"id"_s, s.id}, {u"section"_s, s.id},
                                                   {u"sectionTitle"_s, s.title}, {u"title"_s, s.title}, {u"description"_s, QString()}}});
        ++order;
    }
    for (const SettingEntry &s : m_settings) {
        const QString st = sectionTitle(s.section);
        const int sc = score(s.title, s.description + u' ' + s.keywords.join(u' ') + u' ' + st);
        if (sc >= 0)
            hits.append({sc, order, QVariantMap{{u"kind"_s, u"setting"_s}, {u"id"_s, s.id}, {u"section"_s, s.section},
                                               {u"sectionTitle"_s, st}, {u"title"_s, s.title}, {u"description"_s, s.description}}});
        ++order;
    }
    std::stable_sort(hits.begin(), hits.end(), [](const Hit &a, const Hit &b) { return a.score != b.score ? a.score > b.score : a.order < b.order; });
    QVariantList out;
    for (const Hit &h : hits)
        out << h.row;
    return out;
}

Catalog *Catalog::withBuiltins(QObject *parent)
{
    auto *c = new Catalog(parent);
    const auto sec = [&](const char16_t *id, const QString &title, const char16_t *icon, const char16_t *comp, QStringList kw) {
        c->addSection({QString::fromUtf16(id), title, QString::fromUtf16(icon), QString::fromUtf16(comp), kw});
    };
    sec(u"appearance", tr("Appearance"), u"appearance", u"AppearancePage.qml",
        {tr("theme"), tr("dark"), tr("light"), tr("colour"), tr("color"), tr("motion"), tr("animation")});
    sec(u"display", tr("Displays"), u"display", u"DisplayPage.qml",
        {tr("screen"), tr("monitor"), tr("resolution"), tr("refresh"), tr("scale"), tr("size")});
    sec(u"dock", tr("Dock"), u"dock", u"DockPage.qml", {tr("launcher"), tr("taskbar"), tr("icons")});
    sec(u"desktop", tr("Desktop and windows"), u"desktop", u"DesktopPage.qml",
        {tr("tiling"), tr("windows"), tr("workspace"), tr("keyboard"), tr("shortcuts"), tr("keys")});
    sec(u"sound", tr("Sound"), u"sound", u"SoundPage.qml",
        {tr("audio"), tr("volume"), tr("speaker"), tr("microphone"), tr("headphones"), tr("mute")});
    sec(u"network", tr("Network and Bluetooth"), u"network", u"NetworkPage.qml",
        {tr("wifi"), tr("wi-fi"), tr("internet"), tr("wireless"), tr("bluetooth"), tr("ethernet")});
    sec(u"apps", tr("Apps"), u"apps", u"AppsPage.qml",
        {tr("web apps"), tr("applications"), tr("programs"), tr("install"), tr("remove"), tr("storage"), tr("sign in"), tr("pwa")});
    sec(u"about", tr("About"), u"about", u"AboutPage.qml",
        {tr("version"), tr("device"), tr("name"), tr("storage"), tr("memory"), tr("processor"), tr("hardware")});

    const auto set = [&](const char16_t *id, const char16_t *section, const QString &title, const QString &desc, QStringList kw) {
        c->addSetting({QString::fromUtf16(id), QString::fromUtf16(section), title, desc, kw});
    };
    set(u"appearance.mode", u"appearance", tr("Appearance"), tr("Dark, light, or follow the time of day."),
        {tr("dark mode"), tr("light mode"), tr("auto"), tr("night")});
    set(u"appearance.theme", u"appearance", tr("Theme"), tr("The colours of taOS and its apps."),
        {tr("graphite"), tr("colours"), tr("palette"), tr("accent")});
    set(u"appearance.windowButtons", u"appearance", tr("Window buttons"), tr("Which side the close, minimise and full size buttons are on."),
        {tr("close"), tr("minimise"), tr("title bar"), tr("left"), tr("right")});
    set(u"appearance.reduceMotion", u"appearance", tr("Reduce motion"), tr("Fewer animations."),
        {tr("animation"), tr("accessibility"), tr("transitions")});
    set(u"display.screen", u"display", tr("Screen"), tr("Resolution, refresh rate and size of what is shown."),
        {tr("resolution"), tr("monitor"), tr("native")});
    set(u"display.scale", u"display", tr("Size of text and icons"), tr("Make everything larger or smaller on this screen."),
        {tr("scale"), tr("zoom"), tr("dpi"), tr("hidpi")});
    set(u"display.refresh", u"display", tr("Refresh rate"), tr("How many times a second the screen updates."),
        {tr("hz"), tr("hertz"), tr("fps"), tr("smooth")});
    set(u"display.recommended", u"display", tr("Use recommended settings"), tr("The screen's own resolution and a comfortable size."),
        {tr("native"), tr("reset"), tr("default")});
    set(u"dock.position", u"dock", tr("Position on screen"), tr("Bottom, left or right edge."), {tr("edge"), tr("side"), tr("bottom")});
    set(u"dock.attached", u"dock", tr("Attached to the edge"), tr("Off: the dock floats a little away from the edge."), {tr("floating"), tr("margin")});
    set(u"dock.autohide", u"dock", tr("Hide automatically"), tr("The dock slides away until you move to the edge."), {tr("auto-hide"), tr("autohide"), tr("hidden")});
    set(u"dock.size", u"dock", tr("Icon size"), tr("Small, medium or large."), {tr("big"), tr("small"), tr("large")});
    set(u"dock.magnify", u"dock", tr("Magnification"), tr("Icons grow as the pointer moves over them."), {tr("zoom"), tr("hover")});
    set(u"dock.hideInTiling", u"dock", tr("Hide in tiling mode"), tr("The dock stays out of the way on tiling workspaces."), {tr("tiling")});
    set(u"desktop.mode", u"desktop", tr("Window style"), tr("Desktop: windows float. Tiling: windows fill the screen side by side."),
        {tr("tiling"), tr("floating"), tr("workspace"), tr("layout")});
    set(u"desktop.shortcuts", u"desktop", tr("Keyboard shortcuts"), tr("The keys that control taOS."), {tr("keys"), tr("hotkeys"), tr("bindings")});
    set(u"sound.output", u"sound", tr("Output"), tr("Where sound plays."), {tr("speaker"), tr("headphones"), tr("hdmi")});
    set(u"sound.volume", u"sound", tr("Volume"), tr("How loud the output is."), {tr("loud"), tr("quiet")});
    set(u"sound.mute", u"sound", tr("Mute"), tr("Silence the output."), {tr("silent")});
    set(u"sound.input", u"sound", tr("Input"), tr("Which microphone is used."), {tr("microphone"), tr("mic")});
    set(u"sound.inputVolume", u"sound", tr("Input volume"), tr("How sensitive the microphone is."), {tr("microphone"), tr("gain")});
    set(u"sound.inputMute", u"sound", tr("Mute microphone"), tr("Stop the microphone from listening."), {tr("mic")});
    set(u"network.wifi", u"network", tr("Wi-Fi"), tr("Turn Wi-Fi on or off and join a network."), {tr("wifi"), tr("wireless"), tr("internet"), tr("password")});
    set(u"network.status", u"network", tr("Connection"), tr("How this device is connected right now."), {tr("ethernet"), tr("cable"), tr("online")});
    set(u"network.bluetooth", u"network", tr("Bluetooth"), tr("Turn Bluetooth on or off and connect devices."), {tr("headphones"), tr("pair"), tr("keyboard"), tr("mouse")});
    set(u"apps.web", u"apps", tr("Web apps"), tr("Open, rename, clone, clear the data of or remove a web app."),
        {tr("youtube"), tr("website"), tr("clone"), tr("account"), tr("sign out"), tr("clear data")});
    set(u"apps.containers", u"apps", tr("Container apps"), tr("Apps installed on the controller."), {tr("docker"), tr("service"), tr("remove")});
    set(u"apps.native", u"apps", tr("taOS apps"), tr("The apps that come with taOS."), {tr("open"), tr("launch")});
    set(u"about.device", u"about", tr("Device name"), tr("The name this device has on your network."), {tr("hostname")});
    set(u"about.version", u"about", tr("taOS version"), tr("The version of taOS that is running."), {tr("update"), tr("release")});
    set(u"about.hardware", u"about", tr("Hardware"), tr("Processor, memory and storage."), {tr("cpu"), tr("ram"), tr("disk"), tr("gpu")});
    set(u"about.activity", u"about", tr("Activity"), tr("Open live system activity."), {tr("monitor"), tr("usage"), tr("load")});
    return c;
}

} // namespace TaosSettings
