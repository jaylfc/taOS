fn main() {
    use glib::closure;
    let v = std::rc::Rc::new(1);
    closure!(#[strong] self, move |x| {});
}
