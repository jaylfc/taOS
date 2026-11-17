//! BlurScene: draws the card (blurred wallpaper + translucent surface tint + text).
//! The wallpaper itself is a gtk::Picture underneath; this widget redraws the same
//! texture inside push_blur, clipped to the card's rounded rect (SPEC B3).
use std::cell::{Cell, RefCell};

use gtk::{gdk, glib, graphene, gsk, prelude::*, subclass::prelude::*};

pub const CARD_H: f32 = 320.0;
pub const CARD_MARGIN: f32 = 16.0;
pub const CARD_BOTTOM_GAP: f32 = 120.0;
pub const CARD_RADIUS: f32 = 16.0;
const CARD_PAD: f32 = 20.0;

mod imp {
    use super::*;

    pub struct BlurScene {
        pub texture: RefCell<Option<gdk::Texture>>,
        /// 0.0 = hidden below the bottom edge, 1.0 = shown (may overshoot).
        pub progress: Cell<f64>,
        pub blur_on: Cell<bool>,
        pub radius: Cell<f64>,
        pub label: RefCell<Option<gtk::Label>>,
    }

    impl Default for BlurScene {
        fn default() -> Self {
            Self {
                texture: RefCell::new(None),
                progress: Cell::new(0.0),
                blur_on: Cell::new(true),
                radius: Cell::new(24.0),
                label: RefCell::new(None),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for BlurScene {
        const NAME: &'static str = "PocBlurScene";
        type Type = super::BlurScene;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for BlurScene {
        fn dispose(&self) {
            if let Some(l) = self.label.take() {
                l.unparent();
            }
        }
    }

    impl WidgetImpl for BlurScene {
        fn size_allocate(&self, width: i32, _height: i32, _baseline: i32) {
            if let Some(l) = self.label.borrow().as_ref() {
                let w = (width - 2 * (CARD_MARGIN + CARD_PAD) as i32).max(1);
                let (_, nat, _, _) = l.measure(gtk::Orientation::Vertical, w);
                l.allocate(w, nat.max(1), -1, None);
            }
        }

        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width() as f32, obj.height() as f32);
            let shown_y = h - CARD_BOTTOM_GAP - CARD_H;
            let y = h + (shown_y - h) * self.progress.get() as f32;
            if y >= h || w <= 0.0 {
                return; // fully below the bottom edge
            }
            let Some(tex) = self.texture.borrow().clone() else { return };
            let card = graphene::Rect::new(CARD_MARGIN, y, w - 2.0 * CARD_MARGIN, CARD_H);
            snapshot.push_rounded_clip(&gsk::RoundedRect::from_rect(card, CARD_RADIUS));
            if self.blur_on.get() {
                let r = self.radius.get();
                // cover-fit rect, identical to gtk::Picture ContentFit::Cover
                let (tw, th) = (tex.width() as f32, tex.height() as f32);
                let s = (w / tw).max(h / th);
                let (dw, dh) = (tw * s, th * s);
                let cover = graphene::Rect::new((w - dw) / 2.0, (h - dh) / 2.0, dw, dh);
                snapshot.push_blur(r);
                // bound the blur input to the card plus two radii
                snapshot.push_clip(&card.inset_r(-(r as f32) * 2.0, -(r as f32) * 2.0));
                snapshot.append_texture(&tex, &cover);
                snapshot.pop();
                snapshot.pop();
            }
            let tint = if adw::StyleManager::default().is_dark() {
                gdk::RGBA::new(0.09, 0.09, 0.10, 0.55) // #17171A @ 55%
            } else {
                gdk::RGBA::new(1.0, 1.0, 1.0, 0.55) // #FFFFFF @ 55%
            };
            snapshot.append_color(&tint, &card);
            snapshot.pop();
            if let Some(l) = self.label.borrow().as_ref() {
                snapshot.save();
                snapshot.translate(&graphene::Point::new(CARD_MARGIN + CARD_PAD, y + CARD_PAD));
                obj.snapshot_child(l, snapshot);
                snapshot.restore();
            }
        }
    }
}

glib::wrapper! {
    pub struct BlurScene(ObjectSubclass<imp::BlurScene>) @extends gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl BlurScene {
    pub fn new(texture: Option<gdk::Texture>, blur_on: bool, radius: f64, text: &str) -> Self {
        let obj: Self = glib::Object::new();
        let imp = obj.imp();
        *imp.texture.borrow_mut() = texture;
        imp.blur_on.set(blur_on);
        imp.radius.set(radius);
        let label = gtk::Label::new(Some(text));
        label.add_css_class("poc-card-text");
        label.set_halign(gtk::Align::Start);
        label.set_wrap(true);
        label.set_parent(&obj);
        *imp.label.borrow_mut() = Some(label);
        obj.set_can_target(false);
        obj.set_hexpand(true);
        obj.set_vexpand(true);
        obj
    }

    pub fn progress(&self) -> f64 {
        self.imp().progress.get()
    }

    pub fn set_progress(&self, v: f64) {
        self.imp().progress.set(v);
        self.queue_draw();
    }
}
