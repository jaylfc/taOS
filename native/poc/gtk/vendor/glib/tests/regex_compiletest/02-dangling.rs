fn main() {
    let r = glib::Regex::new(
        "hello",
        glib::RegexCompileFlags::empty(),
        glib::RegexMatchFlags::empty(),
    )
    .unwrap()
    .unwrap();
    let s = glib::GString::from("hello");
    let match_info = r
        .match_(s.as_gstr(), glib::RegexMatchFlags::empty())
        .expect("should match");
    dbg!(match_info.fetch_all());
    drop(s);
}
