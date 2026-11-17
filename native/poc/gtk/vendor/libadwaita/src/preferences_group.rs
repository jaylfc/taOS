// Take a look at the license at the top of the repository in the LICENSE file.

#[cfg(feature = "v1_8")]
#[cfg_attr(docsrs, doc(cfg(feature = "v1_8")))]
use std::ptr;

#[cfg(feature = "v1_8")]
#[cfg_attr(docsrs, doc(cfg(feature = "v1_8")))]
use glib::translate::*;

use crate::PreferencesGroup;
#[cfg(feature = "v1_8")]
#[cfg_attr(docsrs, doc(cfg(feature = "v1_8")))]
use crate::ffi;

impl PreferencesGroup {
    #[cfg(feature = "v1_8")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v1_8")))]
    #[doc(alias = "adw_prefences_group_bind_model")]
    #[doc(alias = "bind_model")]
    pub fn unbind_model(&self) {
        unsafe {
            ffi::adw_preferences_group_bind_model(
                self.to_glib_none().0,
                ptr::null_mut(),
                None,
                ptr::null_mut(),
                None,
            )
        }
    }
}
