fn main() {
    use glib::closure;
    let v = std::rc::Rc::new(1);
    closure!(#[upgrade_or_else] |x| lol, #[strong] v, move || {println!("foo");});
}
