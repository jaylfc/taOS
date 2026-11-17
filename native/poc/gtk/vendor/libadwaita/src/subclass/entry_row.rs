use glib::prelude::*;
use glib::subclass::prelude::*;

use crate::EntryRow;
use crate::subclass::prelude::PreferencesRowImpl;

pub trait EntryRowImpl: PreferencesRowImpl + ObjectSubclass<Type: IsA<EntryRow>> {}

unsafe impl<T: EntryRowImpl> IsSubclassable<T> for EntryRow {}
