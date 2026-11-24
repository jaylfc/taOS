#include "glass.h"

GlassSpec GlassSpec::forTheme(const Theme &t) {
    GlassSpec g;
    g.tint = t.glassTint();
    g.veil = t.glassVeil();
    g.stroke = t.glassStroke();
    g.text = t.text();
    g.placeholder = t.textSecondary();
    g.scrim = t.scrim();
    g.blurRadius = t.blurRadius();
    return g;
}
