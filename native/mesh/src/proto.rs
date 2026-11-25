// SPDX-License-Identifier: AGPL-3.0-or-later
//! A small protobuf wire-format reader and writer.
//!
//! Only the wire format (varint, 64-bit, length-delimited, 32-bit) is
//! implemented here; field numbers come from the Meshtastic `.proto` files
//! (see `meshtastic.rs`). Unknown fields are skipped, never an error, so a
//! newer firmware does not stop decoding. The raw bytes are always kept by
//! the caller.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeError(pub String);

impl fmt::Display for DecodeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "protobuf decode error: {}", self.0)
    }
}
impl std::error::Error for DecodeError {}

#[derive(Debug, Clone, PartialEq)]
pub enum Val<'a> {
    Varint(u64),
    Fixed64(u64),
    Len(&'a [u8]),
    Fixed32(u32),
}

impl<'a> Val<'a> {
    pub fn as_u64(&self) -> u64 {
        match self {
            Val::Varint(v) | Val::Fixed64(v) => *v,
            Val::Fixed32(v) => u64::from(*v),
            Val::Len(_) => 0,
        }
    }
    pub fn as_u32(&self) -> u32 {
        self.as_u64() as u32
    }
    pub fn as_i32(&self) -> i32 {
        self.as_u64() as u32 as i32
    }
    /// sint32 zigzag
    pub fn as_sint32(&self) -> i32 {
        let n = self.as_u64() as u32;
        ((n >> 1) as i32) ^ -((n & 1) as i32)
    }
    pub fn as_f32(&self) -> f32 {
        f32::from_bits(self.as_u64() as u32)
    }
    pub fn as_bool(&self) -> bool {
        self.as_u64() != 0
    }
    pub fn as_bytes(&self) -> &'a [u8] {
        match self {
            Val::Len(b) => b,
            _ => &[],
        }
    }
}

pub struct Fields<'a> {
    buf: &'a [u8],
    pos: usize,
    failed: bool,
}

impl<'a> Fields<'a> {
    pub fn new(buf: &'a [u8]) -> Self {
        Fields {
            buf,
            pos: 0,
            failed: false,
        }
    }

    fn varint(&mut self) -> Result<u64, DecodeError> {
        let mut v: u64 = 0;
        let mut shift = 0;
        loop {
            let b = *self
                .buf
                .get(self.pos)
                .ok_or_else(|| DecodeError("truncated varint".into()))?;
            self.pos += 1;
            if shift >= 64 {
                return Err(DecodeError("varint too long".into()));
            }
            v |= u64::from(b & 0x7f) << shift;
            if b & 0x80 == 0 {
                return Ok(v);
            }
            shift += 7;
        }
    }

    fn take(&mut self, n: usize) -> Result<&'a [u8], DecodeError> {
        let end = self.pos.checked_add(n).filter(|e| *e <= self.buf.len());
        match end {
            Some(e) => {
                let s = &self.buf[self.pos..e];
                self.pos = e;
                Ok(s)
            }
            None => Err(DecodeError("truncated field".into())),
        }
    }
}

impl<'a> Iterator for Fields<'a> {
    type Item = Result<(u32, Val<'a>), DecodeError>;
    fn next(&mut self) -> Option<Self::Item> {
        if self.failed || self.pos >= self.buf.len() {
            return None;
        }
        let r = (|| {
            let key = self.varint()?;
            let field = (key >> 3) as u32;
            if field == 0 {
                return Err(DecodeError("field number 0".into()));
            }
            let v = match key & 7 {
                0 => Val::Varint(self.varint()?),
                1 => {
                    let b = self.take(8)?;
                    Val::Fixed64(u64::from_le_bytes(b.try_into().unwrap()))
                }
                2 => {
                    let n = self.varint()? as usize;
                    Val::Len(self.take(n)?)
                }
                5 => {
                    let b = self.take(4)?;
                    Val::Fixed32(u32::from_le_bytes(b.try_into().unwrap()))
                }
                w => return Err(DecodeError(format!("unsupported wire type {w}"))),
            };
            Ok((field, v))
        })();
        if r.is_err() {
            self.failed = true;
        }
        Some(r)
    }
}

#[derive(Default)]
pub struct Writer {
    pub buf: Vec<u8>,
}

impl Writer {
    pub fn new() -> Self {
        Writer::default()
    }
    fn varint_raw(&mut self, mut v: u64) {
        loop {
            let b = (v & 0x7f) as u8;
            v >>= 7;
            if v == 0 {
                self.buf.push(b);
                return;
            }
            self.buf.push(b | 0x80);
        }
    }
    fn key(&mut self, field: u32, wt: u8) {
        self.varint_raw((u64::from(field) << 3) | u64::from(wt));
    }
    pub fn varint(&mut self, field: u32, v: u64) {
        self.key(field, 0);
        self.varint_raw(v);
    }
    pub fn fixed32(&mut self, field: u32, v: u32) {
        self.key(field, 5);
        self.buf.extend_from_slice(&v.to_le_bytes());
    }
    pub fn bytes(&mut self, field: u32, b: &[u8]) {
        self.key(field, 2);
        self.varint_raw(b.len() as u64);
        self.buf.extend_from_slice(b);
    }
    pub fn finish(self) -> Vec<u8> {
        self.buf
    }
}
