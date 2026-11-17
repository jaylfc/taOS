fn main() {
    use glib::clone;
    let v = std::rc::Rc::new(1);
    clone!(#[upgrade_or_panic] #[strong] v, move || {println!("foo");});
}
