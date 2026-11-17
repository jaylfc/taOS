use glib::prelude::*;
use glib::subclass::prelude::*;

use crate::SpinRow;
use crate::subclass::prelude::ActionRowImpl;

pub trait SpinRowImpl: ActionRowImpl + ObjectSubclass<Type: IsA<SpinRow>> {}

unsafe impl<T: SpinRowImpl> IsSubclassable<T> for SpinRow {}
