fn main() {
    use glib::clone;
    let v = std::rc::Rc::new(1);
    clone!(#[strong] v, async {println!("foo");});
}
