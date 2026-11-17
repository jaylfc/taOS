// Take a look at the license at the top of the repository in the LICENSE file.

use std::rc::Rc;

use glib::clone;

#[test]
fn clone() {
    let _ = clone!(move || {});
    let fut = clone!(async move {});
    drop(fut);

    let x = 1;
    let _ = clone!(move || {
        println!("foo {x}");
        1
    });

    let x = 1;
    let y = String::from("123");
    let v = Rc::new(1);
    let _ = clone!(
        #[strong]
        v,
        move || {
            println!("foo {x} {y} {v}");
            1
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[strong(rename_to = y)]
        v,
        move || {
            println!("foo {y}");
            1
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[strong(rename_to = y)]
        Rc::strong_count(&v),
        move || {
            println!("foo {y}");
            1
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[strong]
        v,
        move |a: i32, b: &str| {
            println!("foo {a} {b} {v}");
            1
        }
    );

    let x = 1;
    let y = String::from("123");
    let v = Rc::new(1);
    let fut = clone!(
        #[strong]
        v,
        async move {
            println!("foo {x} {y} {v}");
            1
        }
    );
    drop(fut);

    let v = Rc::new(1);
    let _ = clone!(
        #[weak]
        v,
        move || {
            println!("foo {v}");
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[weak]
        v,
        #[upgrade_or_else]
        || None::<i32>,
        move || {
            println!("foo {v}");
            Some(1)
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[weak]
        v,
        #[upgrade_or]
        None::<i32>,
        move || {
            println!("foo {v}");
            Some(1)
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[weak]
        v,
        #[upgrade_or_default]
        move || {
            println!("foo {v}");
            Some(1)
        }
    );

    let v = Rc::new(1);
    let w = Rc::new(2);
    let x = Rc::new(3);
    let _ = clone!(
        #[weak]
        v,
        #[weak]
        w,
        #[upgrade_or_else]
        || {
            let x: Rc<i32> = x;
            Some(*x)
        },
        move || {
            println!("foo {v} {w}");
            Some(1)
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[weak]
        v,
        #[upgrade_or_panic]
        move || {
            println!("foo {v}");
            Some(1)
        }
    );

    let v = Rc::new(1);
    let _ = clone!(
        #[weak_allow_none]
        v,
        move || {
            println!("foo {}", v.unwrap());
            Some(1)
        }
    );

    let v = "123";
    let _ = clone!(
        #[to_owned]
        v,
        move || {
            println!("foo {v}");
            1
        }
    );
}

#[test]
fn clone_failures() {
    let t = trybuild::TestCases::new();

    t.compile_fail("tests/clone_compiletest/01-clone-empty.rs");
    t.compile_fail("tests/clone_compiletest/02-clone-no-move.rs");
    t.compile_fail("tests/clone_compiletest/03-clone-self-no-rename.rs");
    t.compile_fail("tests/clone_compiletest/04-clone-self-field-no-rename.rs");
    t.compile_fail("tests/clone_compiletest/05-clone-multiple-rename.rs");
    t.compile_fail("tests/clone_compiletest/06-clone-unsupported-property.rs");
    t.compile_fail("tests/clone_compiletest/07-clone-string-rename.rs");
    t.compile_fail("tests/clone_compiletest/08-clone-upgrade-or-else-bool.rs");
    t.compile_fail("tests/clone_compiletest/09-clone-upgrade-or-invalid.rs");
    t.compile_fail("tests/clone_compiletest/10-clone-unsupported-attribute.rs");
    t.compile_fail("tests/clone_compiletest/11-clone-watch.rs");
    t.compile_fail("tests/clone_compiletest/12-clone-duplicate-attribute.rs");
    t.compile_fail("tests/clone_compiletest/13-clone-no-attribute.rs");
    t.compile_fail("tests/clone_compiletest/14-clone-upgrade-or-else-first.rs");
    t.compile_fail("tests/clone_compiletest/15-clone-upgrade-or-else-with-param.rs");
    t.compile_fail("tests/clone_compiletest/16-clone-upgrade-or-else-async.rs");
    t.compile_fail("tests/clone_compiletest/17-clone-upgrade-or-panic-first.rs");
    t.compile_fail("tests/clone_compiletest/18-clone-async-no-move.rs");
    t.compile_fail("tests/clone_compiletest/19-clone-block-no-parens.rs");
}

// Ensures that no warning are emitted if the return value is a unit tuple.
#[test]
fn clone_unit_tuple_return() {
    let t = trybuild::TestCases::new();

    t.pass("tests/clone_compiletest/20-clone-unit-tuple-1.rs");
    t.pass("tests/clone_compiletest/21-clone-unit-tuple-2.rs");
    t.pass("tests/clone_compiletest/22-clone-unit-tuple-3.rs");
    t.pass("tests/clone_compiletest/23-clone-unit-tuple-4.rs");
    t.pass("tests/clone_compiletest/24-clone-unit-tuple-5.rs");
}
