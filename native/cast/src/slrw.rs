// SPDX-License-Identifier: AGPL-3.0-or-later
//! Annex-B H.264 (x264 with `aud=1`, no B-frames) to SLRW encoded records.
//!
//! A port of the proven adapter. The timestamp of frame n is n * 1e6 / fps
//! microseconds (the capture stage runs at a constant frame rate). Every
//! record is flushed as soon as it is complete so frames never arrive in
//! bursts. Bytes before the first access unit delimiter (NAL type 9) are
//! dropped, and the stream starts on an IDR.

use std::io::{self, Read, Write};

const AUD: [u8; 4] = [0, 0, 1, 9];

fn find(hay: &[u8], needle: &[u8], from: usize) -> Option<usize> {
    if hay.len() < needle.len() || from > hay.len() - needle.len() {
        return None;
    }
    (from..=hay.len() - needle.len()).find(|&i| &hay[i..i + needle.len()] == needle)
}

/// True when the access unit contains an IDR slice (NAL type 5).
fn has_idr(au: &[u8]) -> bool {
    let mut i = 0;
    while let Some(j) = find(au, &[0, 0, 1], i) {
        if j + 3 >= au.len() {
            return false;
        }
        if au[j + 3] & 0x1f == 5 {
            return true;
        }
        i = j + 3;
    }
    false
}

pub struct Adapter<W: Write> {
    out: W,
    fps: f64,
    buf: Vec<u8>,
    started: bool,
    frames: u64,
    wrote_magic: bool,
}

impl<W: Write> Adapter<W> {
    pub fn new(out: W, fps: f64) -> Self {
        Adapter {
            out,
            fps,
            buf: Vec::new(),
            started: false,
            frames: 0,
            wrote_magic: false,
        }
    }

    pub fn frames(&self) -> u64 {
        self.frames
    }

    fn emit(&mut self, au: &[u8]) -> io::Result<()> {
        if au.is_empty() {
            return Ok(());
        }
        let key = has_idr(au);
        if self.frames == 0 && !key {
            return Ok(()); // start on an IDR
        }
        let pts = (self.frames as f64 * 1e6 / self.fps) as u64;
        let mut rec = Vec::with_capacity(au.len() + 14);
        rec.push(3u8);
        rec.extend_from_slice(&pts.to_le_bytes());
        rec.extend_from_slice(&((au.len() + 1) as u32).to_le_bytes());
        rec.push(0x02 | u8::from(key)); // Annex B flag | keyframe flag
        rec.extend_from_slice(au);
        self.out.write_all(&rec)?;
        self.out.flush()?;
        self.frames += 1;
        Ok(())
    }

    /// Feed bytes as they arrive; complete access units are written at once.
    pub fn push(&mut self, chunk: &[u8]) -> io::Result<()> {
        if !self.wrote_magic {
            self.out.write_all(b"SLRW")?;
            self.out.flush()?;
            self.wrote_magic = true;
        }
        self.buf.extend_from_slice(chunk);
        if !self.started {
            match find(&self.buf, &AUD, 0) {
                None => {
                    let keep = self.buf.len().saturating_sub(3);
                    self.buf.drain(..keep);
                    return Ok(());
                }
                Some(j) => {
                    self.buf.drain(..j);
                    self.started = true;
                }
            }
        }
        while let Some(j) = find(&self.buf, &AUD, 4) {
            let cut = if self.buf[j - 1] == 0 { j - 1 } else { j };
            if cut == 0 {
                break;
            }
            let au: Vec<u8> = self.buf.drain(..cut).collect();
            self.emit(&au)?;
        }
        Ok(())
    }

    /// Flush the last access unit and write the end record.
    pub fn finish(&mut self) -> io::Result<()> {
        let rest = std::mem::take(&mut self.buf);
        if self.started {
            self.emit(&rest)?;
        }
        let mut end = vec![0u8];
        end.extend_from_slice(&[0u8; 12]);
        self.out.write_all(&end)?;
        self.out.flush()
    }
}

/// Run the adapter until the input ends.
pub fn run<R: Read, W: Write>(mut input: R, output: W, fps: f64) -> io::Result<u64> {
    let mut a = Adapter::new(output, fps);
    let mut chunk = vec![0u8; 1 << 20];
    loop {
        let n = match input.read(&mut chunk) {
            Ok(0) => break,
            Ok(n) => n,
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e),
        };
        a.push(&chunk[..n])?;
    }
    a.finish()?;
    Ok(a.frames())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One access unit: AUD, optional SPS, then a slice of the given NAL type.
    fn au(idr: bool, pad: usize) -> Vec<u8> {
        let mut v = vec![0, 0, 0, 1, 9, 0xf0];
        if idr {
            v.extend_from_slice(&[0, 0, 0, 1, 0x67, 1, 2, 3]);
            v.extend_from_slice(&[0, 0, 1, 0x65]);
        } else {
            v.extend_from_slice(&[0, 0, 1, 0x41]);
        }
        v.extend(std::iter::repeat_n(0x77, pad));
        v
    }

    fn records(out: &[u8]) -> Vec<(u64, bool, usize)> {
        assert_eq!(&out[..4], b"SLRW");
        let mut i = 4;
        let mut r = vec![];
        while i < out.len() {
            let kind = out[i];
            let pts = u64::from_le_bytes(out[i + 1..i + 9].try_into().unwrap());
            let len = u32::from_le_bytes(out[i + 9..i + 13].try_into().unwrap()) as usize;
            if kind == 0 {
                assert_eq!(i + 13, out.len(), "end record is last");
                break;
            }
            assert_eq!(kind, 3);
            let flags = out[i + 13];
            assert_eq!(flags & 0x02, 0x02);
            r.push((pts, flags & 1 == 1, len - 1));
            i += 13 + len;
        }
        r
    }

    #[test]
    fn counts_frames_pts_and_keyframes() {
        let mut s = b"status text from the recorder\n".to_vec();
        s.extend(au(true, 10));
        s.extend(au(false, 5));
        s.extend(au(false, 5));
        let mut out = Vec::new();
        let n = run(&s[..], &mut out, 30.0).unwrap();
        assert_eq!(n, 3);
        let r = records(&out);
        assert_eq!(r.len(), 3);
        assert_eq!(r[0].0, 0);
        assert_eq!(r[1].0, 33333);
        assert_eq!(r[2].0, 66666);
        assert!(r[0].1 && !r[1].1);
        assert_eq!(r[1].2, au(false, 5).len());
    }

    #[test]
    fn junk_prefix_dropped_and_waits_for_idr() {
        let mut s = b"junk".to_vec();
        s.extend(au(false, 4)); // P frame before any IDR is skipped
        s.extend(au(true, 4));
        s.extend(au(false, 4));
        let mut out = Vec::new();
        assert_eq!(run(&s[..], &mut out, 30.0).unwrap(), 2);
        let r = records(&out);
        assert_eq!(r[0].0, 0);
        assert!(r[0].1);
    }

    #[test]
    fn byte_at_a_time_gives_same_result() {
        let mut s = au(true, 20);
        s.extend(au(false, 7));
        s.extend(au(false, 7));
        let mut whole = Vec::new();
        run(&s[..], &mut whole, 30.0).unwrap();
        let mut a = Adapter::new(Vec::new(), 30.0);
        for b in &s {
            a.push(&[*b]).unwrap();
        }
        a.finish().unwrap();
        assert_eq!(a.out, whole);
    }

    struct Counting {
        flushes: usize,
        data: Vec<u8>,
    }
    impl Write for Counting {
        fn write(&mut self, b: &[u8]) -> io::Result<usize> {
            self.data.extend_from_slice(b);
            Ok(b.len())
        }
        fn flush(&mut self) -> io::Result<()> {
            self.flushes += 1;
            Ok(())
        }
    }

    #[test]
    fn a_frame_is_flushed_when_the_next_one_starts() {
        let mut a = Adapter::new(
            Counting {
                flushes: 0,
                data: vec![],
            },
            30.0,
        );
        a.push(&au(true, 10)).unwrap();
        let after_first = a.out.flushes;
        a.push(&au(false, 10)).unwrap();
        assert!(
            a.out.flushes > after_first,
            "frame 1 flushed without waiting for EOF"
        );
        assert_eq!(a.frames(), 1);
    }
}
