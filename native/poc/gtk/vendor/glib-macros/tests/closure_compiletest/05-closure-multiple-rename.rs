fn main() {
    use glib::closure;
    let v = std::rc::Rc::new(1);
    closure!(#[strong(rename_to = x, rename_to = y)] self.v, move || {});
}
