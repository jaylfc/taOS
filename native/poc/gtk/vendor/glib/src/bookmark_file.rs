// Take a look at the license at the top of the repository in the LICENSE file.

#[cfg(feature = "v2_66")]
#[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
use crate::DateTime;
use crate::{ffi, translate::*};

crate::wrapper! {
    #[doc(alias = "GBookmarkFile")]
    #[derive(Debug, PartialEq, Eq, PartialOrd, Ord, Hash)]
    pub struct BookmarkFile(Boxed<ffi::GBookmarkFile>);

    match fn {
        copy => |ptr| ffi::g_bookmark_file_copy(mut_override(ptr)),
        free => |ptr| ffi::g_bookmark_file_free(ptr),
        type_ => || ffi::g_bookmark_file_get_type(),
    }
}

impl BookmarkFile {
    #[doc(alias = "g_bookmark_file_new")]
    pub fn new() -> BookmarkFile {
        unsafe { from_glib_full(ffi::g_bookmark_file_new()) }
    }

    #[doc(alias = "g_bookmark_file_add_application")]
    pub fn add_application(&mut self, uri: &str, name: Option<&str>, exec: Option<&str>) {
        unsafe {
            ffi::g_bookmark_file_add_application(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                name.to_glib_none().0,
                exec.to_glib_none().0,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_add_group")]
    pub fn add_group(&mut self, uri: &str, group: &str) {
        unsafe {
            ffi::g_bookmark_file_add_group(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                group.to_glib_none().0,
            );
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_get_added")]
    #[doc(alias = "get_added")]
    pub fn added(&self, uri: &str) -> Result<libc::time_t, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_added(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(ret)
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_get_added_date_time")]
    #[doc(alias = "get_added_date_time")]
    pub fn added_date_time(&self, uri: &str) -> Result<DateTime, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_added_date_time(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_none(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_get_app_info")]
    #[doc(alias = "get_app_info")]
    pub fn app_info(
        &self,
        uri: &str,
        name: &str,
    ) -> Result<(crate::GString, u32, libc::time_t), crate::Error> {
        unsafe {
            let mut exec = std::ptr::null_mut();
            let mut count = std::mem::MaybeUninit::uninit();
            let mut stamp = std::mem::MaybeUninit::uninit();
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_get_app_info(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                name.to_glib_none().0,
                &mut exec,
                count.as_mut_ptr(),
                stamp.as_mut_ptr(),
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok((
                    from_glib_full(exec),
                    count.assume_init(),
                    stamp.assume_init(),
                ))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_get_application_info")]
    #[doc(alias = "get_application_info")]
    pub fn application_info(
        &self,
        uri: &str,
        name: &str,
    ) -> Result<(crate::GString, u32, DateTime), crate::Error> {
        unsafe {
            let mut exec = std::ptr::null_mut();
            let mut count = std::mem::MaybeUninit::uninit();
            let mut stamp = std::ptr::null_mut();
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_get_application_info(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                name.to_glib_none().0,
                &mut exec,
                count.as_mut_ptr(),
                &mut stamp,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok((
                    from_glib_full(exec),
                    count.assume_init(),
                    from_glib_none(stamp),
                ))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_applications")]
    #[doc(alias = "get_applications")]
    pub fn applications(&self, uri: &str) -> Result<Vec<crate::GString>, crate::Error> {
        unsafe {
            let mut length = std::mem::MaybeUninit::uninit();
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_applications(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                length.as_mut_ptr(),
                &mut error,
            );
            if error.is_null() {
                Ok(FromGlibContainer::from_glib_full_num(
                    ret,
                    length.assume_init() as _,
                ))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_description")]
    #[doc(alias = "get_description")]
    pub fn description(&self, uri: &str) -> Result<crate::GString, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_description(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_full(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_groups")]
    #[doc(alias = "get_groups")]
    pub fn groups(&self, uri: &str) -> Result<Vec<crate::GString>, crate::Error> {
        unsafe {
            let mut length = std::mem::MaybeUninit::uninit();
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_groups(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                length.as_mut_ptr(),
                &mut error,
            );
            if error.is_null() {
                Ok(FromGlibContainer::from_glib_full_num(
                    ret,
                    length.assume_init() as _,
                ))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_icon")]
    #[doc(alias = "get_icon")]
    pub fn icon(&self, uri: &str) -> Result<(crate::GString, crate::GString), crate::Error> {
        unsafe {
            let mut href = std::ptr::null_mut();
            let mut mime_type = std::ptr::null_mut();
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_get_icon(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut href,
                &mut mime_type,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok((from_glib_full(href), from_glib_full(mime_type)))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_is_private")]
    #[doc(alias = "get_is_private")]
    pub fn is_private(&self, uri: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_get_is_private(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_mime_type")]
    #[doc(alias = "get_mime_type")]
    pub fn mime_type(&self, uri: &str) -> Result<crate::GString, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_mime_type(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_full(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_get_modified")]
    #[doc(alias = "get_modified")]
    pub fn modified(&self, uri: &str) -> Result<libc::time_t, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_modified(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(ret)
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_get_modified_date_time")]
    #[doc(alias = "get_modified_date_time")]
    pub fn modified_date_time(&self, uri: &str) -> Result<DateTime, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_modified_date_time(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_none(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_size")]
    #[doc(alias = "get_size")]
    pub fn size(&self) -> i32 {
        unsafe { ffi::g_bookmark_file_get_size(mut_override(self.to_glib_none().0)) }
    }

    #[doc(alias = "g_bookmark_file_get_title")]
    #[doc(alias = "get_title")]
    pub fn title(&self, uri: Option<&str>) -> Result<crate::GString, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_title(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_full(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_get_uris")]
    #[doc(alias = "get_uris")]
    pub fn uris(&self) -> Vec<crate::GString> {
        unsafe {
            let mut length = std::mem::MaybeUninit::uninit();

            FromGlibContainer::from_glib_full_num(
                ffi::g_bookmark_file_get_uris(
                    mut_override(self.to_glib_none().0),
                    length.as_mut_ptr(),
                ),
                length.assume_init() as _,
            )
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_get_visited")]
    #[doc(alias = "get_visited")]
    pub fn visited(&self, uri: &str) -> Result<libc::time_t, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_visited(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(ret)
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_get_visited_date_time")]
    #[doc(alias = "get_visited_date_time")]
    pub fn visited_date_time(&self, uri: &str) -> Result<DateTime, crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_get_visited_date_time(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                &mut error,
            );
            if error.is_null() {
                Ok(from_glib_none(ret))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_has_application")]
    pub fn has_application(&self, uri: &str, name: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_has_application(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                name.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_has_group")]
    pub fn has_group(&self, uri: &str, group: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_has_group(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
                group.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_has_item")]
    pub fn has_item(&self, uri: &str) -> bool {
        unsafe {
            from_glib(ffi::g_bookmark_file_has_item(
                mut_override(self.to_glib_none().0),
                uri.to_glib_none().0,
            ))
        }
    }

    #[doc(alias = "g_bookmark_file_load_from_data")]
    pub fn load_from_data(&mut self, data: &[u8]) -> Result<(), crate::Error> {
        let length = data.len() as _;
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_load_from_data(
                self.to_glib_none_mut().0,
                data.to_glib_none().0,
                length,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_load_from_data_dirs")]
    pub fn load_from_data_dirs(
        &mut self,
        file: impl AsRef<std::path::Path>,
    ) -> Result<std::path::PathBuf, crate::Error> {
        unsafe {
            let mut full_path = std::ptr::null_mut();
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_load_from_data_dirs(
                self.to_glib_none_mut().0,
                file.as_ref().to_glib_none().0,
                &mut full_path,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(from_glib_full(full_path))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_load_from_file")]
    pub fn load_from_file(
        &mut self,
        filename: impl AsRef<std::path::Path>,
    ) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_load_from_file(
                self.to_glib_none_mut().0,
                filename.as_ref().to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_move_item")]
    pub fn move_item(&mut self, old_uri: &str, new_uri: Option<&str>) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_move_item(
                self.to_glib_none_mut().0,
                old_uri.to_glib_none().0,
                new_uri.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_remove_application")]
    pub fn remove_application(&mut self, uri: &str, name: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_remove_application(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                name.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_remove_group")]
    pub fn remove_group(&mut self, uri: &str, group: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_remove_group(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                group.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_remove_item")]
    pub fn remove_item(&mut self, uri: &str) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_remove_item(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_set_added")]
    pub fn set_added(&mut self, uri: &str, added: libc::time_t) {
        unsafe {
            ffi::g_bookmark_file_set_added(self.to_glib_none_mut().0, uri.to_glib_none().0, added);
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_set_added_date_time")]
    pub fn set_added_date_time(&mut self, uri: &str, added: &DateTime) {
        unsafe {
            ffi::g_bookmark_file_set_added_date_time(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                added.to_glib_none().0,
            );
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_set_app_info")]
    pub fn set_app_info(
        &mut self,
        uri: &str,
        name: &str,
        exec: &str,
        count: i32,
        stamp: libc::time_t,
    ) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_set_app_info(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                name.to_glib_none().0,
                exec.to_glib_none().0,
                count,
                stamp,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_set_application_info")]
    pub fn set_application_info(
        &mut self,
        uri: &str,
        name: &str,
        exec: &str,
        count: i32,
        stamp: Option<&DateTime>,
    ) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_set_application_info(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                name.to_glib_none().0,
                exec.to_glib_none().0,
                count,
                stamp.to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_set_description")]
    pub fn set_description(&mut self, uri: Option<&str>, description: &str) {
        unsafe {
            ffi::g_bookmark_file_set_description(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                description.to_glib_none().0,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_set_groups")]
    pub fn set_groups(&mut self, uri: &str, groups: &[&str]) {
        let length = groups.len() as _;
        unsafe {
            ffi::g_bookmark_file_set_groups(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                groups.to_glib_none().0,
                length,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_set_icon")]
    pub fn set_icon(&mut self, uri: &str, href: Option<&str>, mime_type: &str) {
        unsafe {
            ffi::g_bookmark_file_set_icon(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                href.to_glib_none().0,
                mime_type.to_glib_none().0,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_set_is_private")]
    pub fn set_is_private(&mut self, uri: &str, is_private: bool) {
        unsafe {
            ffi::g_bookmark_file_set_is_private(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                is_private.into_glib(),
            );
        }
    }

    #[doc(alias = "g_bookmark_file_set_mime_type")]
    pub fn set_mime_type(&mut self, uri: &str, mime_type: &str) {
        unsafe {
            ffi::g_bookmark_file_set_mime_type(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                mime_type.to_glib_none().0,
            );
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_set_modified")]
    pub fn set_modified(&mut self, uri: &str, modified: libc::time_t) {
        unsafe {
            ffi::g_bookmark_file_set_modified(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                modified,
            );
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_set_modified_date_time")]
    pub fn set_modified_date_time(&mut self, uri: &str, modified: &DateTime) {
        unsafe {
            ffi::g_bookmark_file_set_modified_date_time(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                modified.to_glib_none().0,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_set_title")]
    pub fn set_title(&mut self, uri: Option<&str>, title: &str) {
        unsafe {
            ffi::g_bookmark_file_set_title(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                title.to_glib_none().0,
            );
        }
    }

    #[cfg_attr(feature = "v2_66", deprecated = "Since 2.66")]
    #[allow(deprecated)]
    #[doc(alias = "g_bookmark_file_set_visited")]
    pub fn set_visited(&mut self, uri: &str, visited: libc::time_t) {
        unsafe {
            ffi::g_bookmark_file_set_visited(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                visited,
            );
        }
    }

    #[cfg(feature = "v2_66")]
    #[cfg_attr(docsrs, doc(cfg(feature = "v2_66")))]
    #[doc(alias = "g_bookmark_file_set_visited_date_time")]
    pub fn set_visited_date_time(&mut self, uri: &str, visited: &DateTime) {
        unsafe {
            ffi::g_bookmark_file_set_visited_date_time(
                self.to_glib_none_mut().0,
                uri.to_glib_none().0,
                visited.to_glib_none().0,
            );
        }
    }

    #[doc(alias = "g_bookmark_file_to_data")]
    pub fn to_data(&self) -> Result<Vec<u8>, crate::Error> {
        unsafe {
            let mut length = std::mem::MaybeUninit::uninit();
            let mut error = std::ptr::null_mut();
            let ret = ffi::g_bookmark_file_to_data(
                mut_override(self.to_glib_none().0),
                length.as_mut_ptr(),
                &mut error,
            );
            if error.is_null() {
                Ok(FromGlibContainer::from_glib_full_num(
                    ret,
                    length.assume_init() as _,
                ))
            } else {
                Err(from_glib_full(error))
            }
        }
    }

    #[doc(alias = "g_bookmark_file_to_file")]
    pub fn to_file(&self, filename: impl AsRef<std::path::Path>) -> Result<(), crate::Error> {
        unsafe {
            let mut error = std::ptr::null_mut();
            let is_ok = ffi::g_bookmark_file_to_file(
                mut_override(self.to_glib_none().0),
                filename.as_ref().to_glib_none().0,
                &mut error,
            );
            debug_assert_eq!(is_ok == crate::ffi::GFALSE, !error.is_null());
            if error.is_null() {
                Ok(())
            } else {
                Err(from_glib_full(error))
            }
        }
    }
}

impl Default for BookmarkFile {
    fn default() -> Self {
        Self::new()
    }
}
