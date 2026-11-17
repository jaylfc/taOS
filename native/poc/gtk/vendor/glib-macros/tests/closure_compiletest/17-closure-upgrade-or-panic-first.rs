fn main() {
    use glib::closure;
    let v = std::rc::Rc::new(1);
    closure!(#[upgrade_or_panic] #[strong] v, move || {println!("foo");});
}
