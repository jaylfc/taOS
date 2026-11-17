// Take a look at the license at the top of the repository in the LICENSE file.

#[test]
fn closure_failures() {
    let t = trybuild::TestCases::new();

    t.compile_fail("tests/closure_compiletest/01-closure-empty.rs");
    t.compile_fail("tests/closure_compiletest/02-closure-no-move.rs");
    t.compile_fail("tests/closure_compiletest/03-closure-self-no-rename.rs");
    t.compile_fail("tests/closure_compiletest/04-closure-self-field-no-rename.rs");
    t.compile_fail("tests/closure_compiletest/05-closure-multiple-rename.rs");
    t.compile_fail("tests/closure_compiletest/06-closure-unsupported-property.rs");
    t.compile_fail("tests/closure_compiletest/07-closure-string-rename.rs");
    t.compile_fail("tests/closure_compiletest/08-closure-upgrade-or-else-bool.rs");
    t.compile_fail("tests/closure_compiletest/09-closure-upgrade-or-invalid.rs");
    t.compile_fail("tests/closure_compiletest/10-closure-unsupported-attribute.rs");
    t.compile_fail("tests/closure_compiletest/11-closure-multiple-watch.rs");
    t.compile_fail("tests/closure_compiletest/12-closure-duplicate-attribute.rs");
    t.compile_fail("tests/closure_compiletest/13-closure-no-attribute.rs");
    t.compile_fail("tests/closure_compiletest/14-closure-upgrade-or-else-first.rs");
    t.compile_fail("tests/closure_compiletest/15-closure-upgrade-or-else-with-param.rs");
    t.compile_fail("tests/closure_compiletest/16-closure-upgrade-or-else-async.rs");
    t.compile_fail("tests/closure_compiletest/17-closure-upgrade-or-panic-first.rs");
    t.compile_fail("tests/closure_compiletest/18-closure-async-no-move.rs");
    t.compile_fail("tests/closure_compiletest/19-closure-block-no-parens.rs");
}
