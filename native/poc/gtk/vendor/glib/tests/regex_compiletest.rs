#[test]
pub fn test() {
    let t = trybuild::TestCases::new();

    t.pass("tests/regex_compiletest/01-not-dangling.rs");
    t.compile_fail("tests/regex_compiletest/02-dangling.rs");
    t.pass("tests/regex_compiletest/03-static-value.rs");
    t.compile_fail("tests/regex_compiletest/04-nonstatic-value.rs");
    t.compile_fail("tests/regex_compiletest/05-variance.rs");
    t.pass("tests/regex_compiletest/06-property.rs");
}
