fn main() {
    use glib::clone;
    let v = std::rc::Rc::new(1);
    clone!(#[weak] v, #[upgrade_or_else] || ( () ), move || println!("{}", v));
}
