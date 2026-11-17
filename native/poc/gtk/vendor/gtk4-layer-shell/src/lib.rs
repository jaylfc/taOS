#![cfg_attr(docsrs, feature(doc_cfg))]
#![deny(warnings)]
#![allow(rustdoc::redundant_explicit_links)]

#[allow(unused_imports)]
#[allow(clippy::single_component_path_imports)]
use gtk; // Required for the documentation to build without warnings

use glib::translate::{FromGlib, TryFromGlib};
use gtk::prelude::IsA;
use gtk4_layer_shell_sys as ffi;

impl TryFromGlib<ffi::GtkLayerShellLayer> for Layer {
    type Error = glib::translate::GlibNoneError;

    unsafe fn try_from_glib(value: ffi::GtkLayerShellLayer) -> Result<Self, Self::Error> {
        let layer = unsafe { Self::from_glib(value) };
        // If we got an unknown variant, return an error; otherwise, return the value.
        match layer {
            Layer::__Unknown(_) => Err(glib::translate::GlibNoneError),
            _ => Ok(layer),
        }
    }
}

macro_rules! assert_initialized_main_thread {
    () => {
        if !::gtk::is_initialized_main_thread() {
            if ::gtk::is_initialized() {
                panic!("GTK may only be used from the main thread.");
            } else {
                panic!("GTK has not been initialized. Call `gtk::init` first.");
            }
        }
    };
}

/// No-op.
macro_rules! skip_assert_initialized {
    () => {};
}

mod auto;
pub use auto::{
    functions::is_supported, functions::major_version, functions::micro_version,
    functions::minor_version, functions::protocol_version, Edge, KeyboardMode, Layer,
};

mod manual;
pub use manual::*;

pub trait LayerShell: IsA<gtk::Window> {
    /// When auto exclusive zone is enabled, exclusive zone is automatically set to the
    /// size of the `window` + relevant margin. To disable auto exclusive zone, just set the
    /// exclusive zone to 0 or any other fixed value.
    ///
    /// NOTE: you can control the auto exclusive zone by changing the margin on the non-anchored
    /// edge. This behavior is specific to gtk-layer-shell and not part of the underlying protocol
    /// ## `window`
    /// A layer surface.
    #[doc(alias = "gtk_layer_auto_exclusive_zone_enable")]
    fn auto_exclusive_zone_enable(&self) {
        crate::auto::functions::auto_exclusive_zone_enable(self);
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// if the surface's exclusive zone is set to change based on the window's size
    #[doc(alias = "gtk_layer_auto_exclusive_zone_is_enabled")]
    fn auto_exclusive_zone_is_enabled(&self) -> bool {
        crate::auto::functions::auto_exclusive_zone_is_enabled(self)
    }

    /// ## `window`
    /// A layer surface.
    /// ## `edge`
    /// the edge to which the surface may or may not be anchored
    ///
    /// # Returns
    ///
    /// if this surface is anchored to the given edge.
    #[doc(alias = "gtk_layer_get_anchor")]
    #[doc(alias = "get_anchor")]
    fn is_anchor(&self, edge: Edge) -> bool {
        crate::auto::functions::is_anchor(self, edge)
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// the window's exclusive zone (which may have been set manually or automatically)
    #[doc(alias = "gtk_layer_get_exclusive_zone")]
    #[doc(alias = "get_exclusive_zone")]
    fn exclusive_zone(&self) -> i32 {
        crate::auto::functions::exclusive_zone(self)
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// current keyboard interactivity mode for `window`.
    #[doc(alias = "gtk_layer_get_keyboard_mode")]
    #[doc(alias = "get_keyboard_mode")]
    fn keyboard_mode(&self) -> KeyboardMode {
        crate::auto::functions::keyboard_mode(self)
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// the current layer.
    #[doc(alias = "gtk_layer_get_layer")]
    #[doc(alias = "get_layer")]
    fn layer(&self) -> Layer {
        crate::auto::functions::layer(self)
    }

    /// ## `window`
    /// A layer surface.
    /// ## `edge`
    /// the margin edge to get
    ///
    /// # Returns
    ///
    /// the size of the margin for the given edge.
    #[doc(alias = "gtk_layer_get_margin")]
    #[doc(alias = "get_margin")]
    fn margin(&self, edge: Edge) -> i32 {
        crate::auto::functions::margin(self, edge)
    }

    /// NOTE: To get which monitor the surface is actually on, use
    /// `gdk_display_get_monitor_at_surface()`.
    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// the monitor this surface will/has requested to be on.
    #[doc(alias = "gtk_layer_get_monitor")]
    #[doc(alias = "get_monitor")]
    fn monitor(&self) -> Option<gdk::Monitor> {
        crate::auto::functions::monitor(self)
    }

    /// NOTE: this function does not return ownership of the string. Do not free the returned string.
    /// Future calls into the library may invalidate the returned string.
    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// a reference to the namespace property. If namespace is unset, returns the
    /// default namespace("gtk4-layer-shell"). Never returns [`None`].
    #[doc(alias = "gtk_layer_get_namespace")]
    #[doc(alias = "get_namespace")]
    fn namespace(&self) -> Option<glib::GString> {
        crate::auto::functions::namespace(self)
    }

    /// Set the `window` up to be a layer surface once it is mapped. this must be called before
    /// the `window` is realized.
    /// ## `window`
    /// A [`gtk::Window`][crate::gtk::Window] to be turned into a layer surface.
    #[doc(alias = "init_for_window")]
    #[doc(alias = "gtk_layer_init_for_window")]
    fn init_layer_shell(&self) {
        crate::auto::functions::init_for_window(self);
    }

    /// ## `window`
    /// A [`gtk::Window`][crate::gtk::Window] that may or may not have a layer surface.
    ///
    /// # Returns
    ///
    /// if `window` has been initialized as a layer surface.
    #[doc(alias = "gtk_layer_is_layer_window")]
    fn is_layer_window(&self) -> bool {
        crate::auto::functions::is_layer_window(self)
    }

    /// Set whether `window` should be anchored to `edge`.
    /// - If two perpendicular edges are anchored, the surface with be anchored to that corner
    /// - If two opposite edges are anchored, the window will be stretched across the screen in that direction
    ///
    /// Default is [`false`] for each [`Edge`][crate::Edge]
    /// ## `window`
    /// A layer surface.
    /// ## `edge`
    /// A [`Edge`][crate::Edge] this layer surface may be anchored to.
    /// ## `anchor_to_edge`
    /// Whether or not to anchor this layer surface to `edge`.
    #[doc(alias = "gtk_layer_set_anchor")]
    fn set_anchor(&self, edge: Edge, anchor_to_edge: bool) {
        crate::auto::functions::set_anchor(self, edge, anchor_to_edge);
    }

    /// Has no effect unless the surface is anchored to an edge. Requests that the compositor
    /// does not place other surfaces within the given exclusive zone of the anchored edge.
    /// For example, a panel can request to not be covered by maximized windows. See
    /// wlr-layer-shell-unstable-v1.xml for details.
    ///
    /// Default is 0
    /// ## `window`
    /// A layer surface.
    /// ## `exclusive_zone`
    /// The size of the exclusive zone.
    #[doc(alias = "gtk_layer_set_exclusive_zone")]
    fn set_exclusive_zone(&self, exclusive_zone: i32) {
        crate::auto::functions::set_exclusive_zone(self, exclusive_zone);
    }

    /// Sets if/when `window` should receive keyboard events from the compositor, see
    /// GtkLayerShellKeyboardMode for details. To control mouse/touch interactivity use input regions,
    /// see [`61`](https://github.com/wmww/gtk4-layer-shell/issues/61) for details.
    ///
    /// Default is [`KeyboardMode::None`][crate::KeyboardMode::None]
    /// ## `window`
    /// A layer surface.
    /// ## `mode`
    /// The type of keyboard interactivity requested.
    #[doc(alias = "gtk_layer_set_keyboard_mode")]
    fn set_keyboard_mode(&self, mode: KeyboardMode) {
        crate::auto::functions::set_keyboard_mode(self, mode);
    }

    /// Set the "layer" on which the surface appears (controls if it is over top of or below other surfaces). The layer may
    /// be changed on-the-fly in the current version of the layer shell protocol, but on compositors that only support an
    /// older version the `window` is remapped so the change can take effect.
    ///
    /// Default is [`Layer::Top`][crate::Layer::Top]
    /// ## `window`
    /// A layer surface.
    /// ## `layer`
    /// The layer on which this surface appears.
    #[doc(alias = "gtk_layer_set_layer")]
    fn set_layer(&self, layer: Layer) {
        crate::auto::functions::set_layer(self, layer);
    }

    /// Set the margin for a specific `edge` of a `window`. Effects both surface's distance from
    /// the edge and its exclusive zone size (if auto exclusive zone enabled).
    ///
    /// Default is 0 for each [`Edge`][crate::Edge]
    /// ## `window`
    /// A layer surface.
    /// ## `edge`
    /// The [`Edge`][crate::Edge] for which to set the margin.
    /// ## `margin_size`
    /// The margin for `edge` to be set.
    #[doc(alias = "gtk_layer_set_margin")]
    fn set_margin(&self, edge: Edge, margin_size: i32) {
        crate::auto::functions::set_margin(self, edge, margin_size);
    }

    /// Set the output for the window to be placed on, or [`None`] to let the compositor choose.
    /// If the window is currently mapped, it will get remapped so the change can take effect.
    ///
    /// Default is [`None`]
    /// ## `window`
    /// A layer surface.
    /// ## `monitor`
    /// The output this layer surface will be placed on ([`None`] to let the compositor decide).
    #[doc(alias = "gtk_layer_set_monitor")]
    fn set_monitor(&self, monitor: Option<&gdk::Monitor>) {
        crate::auto::functions::set_monitor(self, monitor);
    }

    /// Set the "namespace" of the surface.
    ///
    /// No one is quite sure what this is for, but it probably should be something generic
    /// ("panel", "osk", etc). The `name_space` string is copied, and caller maintains
    /// ownership of original. If the window is currently mapped, it will get remapped so
    /// the change can take effect.
    ///
    /// Default is "gtk4-layer-shell" (which will be used if set to [`None`])
    /// ## `window`
    /// A layer surface.
    /// ## `name_space`
    /// The namespace of this layer surface.
    #[doc(alias = "gtk_layer_set_namespace")]
    fn set_namespace(&self, name_space: Option<&str>) {
        crate::auto::functions::set_namespace(self, name_space);
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// The underlying layer surface Wayland object
    #[doc(alias = "gtk_layer_get_zwlr_layer_surface_v1")]
    #[doc(alias = "get_zwlr_layer_surface_v1")]
    fn zwlr_layer_surface_v1(&self) -> Option<*mut ffi::zwlr_layer_surface_v1> {
        zwlr_layer_surface_v1(self)
    }

    /// ## `window`
    /// A layer surface.
    ///
    /// # Returns
    ///
    /// if the respect_close behavior is enabled, see [`set_respect_close()`][crate::LayerShell::set_respect_close()]
    #[cfg(feature = "v1_3")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v1_3")))]
    #[doc(alias = "gtk_layer_get_respect_close")]
    #[doc(alias = "get_respect_close")]
    fn is_respect_close(&self) -> bool {
        crate::auto::functions::is_respect_close(self)
    }

    /// Compositors may send the `zwlr_layer_surface_v1.closed` event in some cases (such as
    /// when an output is destroyed). Prior to v1.3 this always triggered a GTK `close-request`
    /// signal, which would destroy the window if not intercepted by application code. In v1.3+
    /// this behavior is disabled by default, and can be turned back on by calling this
    /// function with [`true`]. To handle the `.closed` event without destroying your window
    /// turn respect_close on and connect a `close-request` listener that returns [`true`].
    /// ## `window`
    /// A layer surface.
    /// ## `respect_close`
    /// If to forward the .closed event to GTK.
    #[cfg(feature = "v1_3")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v1_3")))]
    #[doc(alias = "gtk_layer_set_respect_close")]
    fn set_respect_close(&self, respect_close: bool) {
        crate::auto::functions::set_respect_close(self, respect_close);
    }
}

// The default implementation is always fine
impl<T: IsA<gtk::Window>> LayerShell for T {}
