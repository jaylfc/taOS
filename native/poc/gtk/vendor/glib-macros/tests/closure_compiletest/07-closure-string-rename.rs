fn main() {
    use glib::closure;
    let v = std::rc::Rc::new(1);
    closure!(#[strong(rename_to = "a")] self.v, move || {});
}
