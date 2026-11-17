//! PocModel: 500-row QAbstractListModel with `title` and `subtitle` roles.
#[cxx_qt::bridge]
pub mod qobject {
    unsafe extern "C++" {
        include!(<QtCore/QAbstractListModel>);
        type QAbstractListModel;
        include!("cxx-qt-lib/qmodelindex.h");
        type QModelIndex = cxx_qt_lib::QModelIndex;
        include!("cxx-qt-lib/qvariant.h");
        type QVariant = cxx_qt_lib::QVariant;
        include!("cxx-qt-lib/qhash.h");
        type QHash_i32_QByteArray = cxx_qt_lib::QHash<cxx_qt_lib::QHashPair_i32_QByteArray>;
    }

    extern "RustQt" {
        #[qobject]
        #[base = QAbstractListModel]
        #[qml_element]
        type PocModel = super::PocModelRust;
    }

    unsafe extern "RustQt" {
        #[cxx_override]
        #[cxx_name = "rowCount"]
        fn row_count(self: &PocModel, parent: &QModelIndex) -> i32;
        #[cxx_override]
        fn data(self: &PocModel, index: &QModelIndex, role: i32) -> QVariant;
        #[cxx_override]
        #[cxx_name = "roleNames"]
        fn role_names(self: &PocModel) -> QHash_i32_QByteArray;
    }
}

use cxx_qt_lib::{QByteArray, QHash, QHashPair_i32_QByteArray, QModelIndex, QString, QVariant};

const ROWS: i32 = 500;
const TITLE: i32 = 0x0100 + 1; // Qt::UserRole + 1
const SUBTITLE: i32 = 0x0100 + 2;

#[derive(Default)]
pub struct PocModelRust;

impl qobject::PocModel {
    pub fn row_count(&self, parent: &QModelIndex) -> i32 {
        if parent.is_valid() { 0 } else { ROWS }
    }

    pub fn data(&self, index: &QModelIndex, role: i32) -> QVariant {
        let r = index.row();
        if !index.is_valid() || r < 0 || r >= ROWS {
            return QVariant::default();
        }
        match role {
            TITLE => QVariant::from(&QString::from(format!("Row {r}"))),
            SUBTITLE => QVariant::from(&QString::from(format!("Second line for row {r}"))),
            _ => QVariant::default(),
        }
    }

    pub fn role_names(&self) -> QHash<QHashPair_i32_QByteArray> {
        let mut h = QHash::<QHashPair_i32_QByteArray>::default();
        h.insert(TITLE, QByteArray::from("title"));
        h.insert(SUBTITLE, QByteArray::from("subtitle"));
        h
    }
}
