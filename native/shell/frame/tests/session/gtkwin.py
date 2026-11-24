import sys, gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib
mode, appid, title = sys.argv[1], sys.argv[2], sys.argv[3]
class A(Gtk.Application):
    def __init__(s): super().__init__(application_id=appid)
    def do_activate(s):
        w = Gtk.ApplicationWindow(application=s, title=title)
        w.set_default_size(520, 300)
        if mode == "header":
            h = Gtk.HeaderBar(); h.set_show_close_button(True); h.set_title(title); w.set_titlebar(h)
        w.add(Gtk.Label(label="GTK3 window (%s)" % mode))
        w.show_all()
A().run([])
