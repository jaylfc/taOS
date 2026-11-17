use glib::object::IsA;
use glib::translate::*;
use gtk4_layer_shell_sys as ffi;

/// ## `window`
/// A layer surface.
///
/// # Returns
///
/// The underlying layer surface Wayland object
#[doc(alias = "gtk_layer_get_zwlr_layer_surface_v1")]
#[doc(alias = "get_zwlr_layer_surface_v1")]
pub fn zwlr_layer_surface_v1(
    window: &impl IsA<gtk::Window>,
) -> Option<*mut ffi::zwlr_layer_surface_v1> {
    assert_initialized_main_thread!();

    unsafe {
        let ptr = ffi::gtk_layer_get_zwlr_layer_surface_v1(window.as_ref().to_glib_none().0);
        if ptr.is_null() {
            None
        } else {
            Some(ptr)
        }
    }
}
