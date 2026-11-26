mod common;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use common::*;
use futures_util::StreamExt;
use taos_client::events::{Backoff, PollWhileVisible, SseOptions};
use taos_client::{Client, Error};
use tokio::time::Instant;
use wiremock::matchers::{header as header_is, method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

fn sse(body: &str) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_raw(body.to_string(), "text/event-stream")
}

fn fast() -> SseOptions {
    SseOptions {
        backoff: Backoff::new(Duration::from_millis(5), Duration::from_millis(20)),
        ..SseOptions::default()
    }
}

#[tokio::test]
async fn parses_multi_line_data_comments_and_heartbeats() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(sse(": connected\n\nevent: custom\nid: 1\ndata: line one\ndata: line two\n\n:keepalive\n\ndata: {\"k\":1}\n\n"))
        .mount(&server)
        .await;
    let mut s = client.sse("/stream", vec![], fast());
    let a = s.next().await.expect("item").expect("event");
    assert_eq!(a.event.as_deref(), Some("custom"));
    assert_eq!(a.data, "line one\nline two");
    assert_eq!(a.id.as_deref(), Some("1"));
    let b = s.next().await.expect("item").expect("event");
    assert_eq!((b.event, b.data.as_str()), (None, "{\"k\":1}"));
    let r = &find(&server, "GET", "/stream").await[0];
    assert_eq!(header(r, "accept").as_deref(), Some("text/event-stream"));
    assert!(header(r, "cookie").is_some(), "stream carries the session");
}

#[tokio::test]
async fn reconnects_with_last_event_id_after_the_server_drops_the_stream() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .and(header_is("last-event-id", "1"))
        .respond_with(sse("id: 2\ndata: second\n\n"))
        .with_priority(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(sse("id: 1\ndata: first\n\n"))
        .mount(&server)
        .await;

    let mut s = client.sse("/stream", vec![], fast());
    assert_eq!(s.next().await.expect("item").expect("event").data, "first");
    assert!(
        matches!(s.next().await.expect("item"), Err(Error::Disconnected)),
        "the drop is reported"
    );
    let second = s.next().await.expect("item").expect("event");
    assert_eq!(
        (second.data.as_str(), second.id.as_deref()),
        ("second", Some("2"))
    );
    let reqs = find(&server, "GET", "/stream").await;
    assert!(reqs.len() >= 2);
    assert!(header(&reqs[0], "last-event-id").is_none());
    assert_eq!(header(&reqs[1], "last-event-id").as_deref(), Some("1"));
}

#[tokio::test]
async fn resume_can_be_turned_off() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(sse("id: 9\ndata: x\n\n"))
        .mount(&server)
        .await;
    let mut s = client.sse(
        "/stream",
        vec![],
        SseOptions {
            resume: false,
            ..fast()
        },
    );
    for _ in 0..2 {
        loop {
            if let Ok(ev) = s.next().await.expect("item") {
                assert_eq!(ev.data, "x");
                break;
            }
        }
    }
    drop(s);
    for r in find(&server, "GET", "/stream").await {
        assert!(header(&r, "last-event-id").is_none());
    }
}

#[tokio::test]
async fn a_401_is_reported_once_and_ends_the_stream() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(
            ResponseTemplate::new(401)
                .set_body_raw(r#"{"error":"Authentication required"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    let mut s = client.sse("/stream", vec![], fast());
    assert!(s
        .next()
        .await
        .expect("item")
        .expect_err("401")
        .is_unauthenticated());
    assert!(s.next().await.is_none());
    tokio::time::sleep(Duration::from_millis(80)).await;
    assert_eq!(
        find(&server, "GET", "/stream").await.len(),
        1,
        "no retry on 401"
    );
}

#[tokio::test]
async fn server_errors_are_retried() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(ResponseTemplate::new(503))
        .up_to_n_times(1)
        .with_priority(1)
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(sse("data: up\n\n"))
        .mount(&server)
        .await;
    let mut s = client.sse("/stream", vec![], fast());
    assert!(matches!(
        s.next().await.expect("item"),
        Err(Error::Server { status: 503, .. })
    ));
    assert_eq!(s.next().await.expect("item").expect("event").data, "up");
}

#[tokio::test]
async fn dropping_the_stream_cancels_it() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/stream"))
        .respond_with(sse("data: x\n\n"))
        .mount(&server)
        .await;
    let mut s = client.sse("/stream", vec![], fast());
    let _ = s.next().await;
    drop(s);
    tokio::time::sleep(Duration::from_millis(40)).await;
    let n = find(&server, "GET", "/stream").await.len();
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(
        find(&server, "GET", "/stream").await.len(),
        n,
        "no reconnects after drop"
    );
}

#[tokio::test(start_paused = true)]
async fn backoff_grows_and_is_capped_at_thirty_seconds() {
    // A closed port refuses instantly, so the only waiting is the backoff
    // sleep, which the paused clock skips.
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    drop(listener);
    let client = Client::builder()
        .base_url(format!("http://127.0.0.1:{port}"))
        .connect_timeout(Duration::from_secs(3600))
        .build()
        .expect("client");
    let mut s = client.sse("/stream", vec![], SseOptions::default());
    let mut stamps = Vec::new();
    for _ in 0..14 {
        let item = s.next().await.expect("item");
        assert!(matches!(item, Err(Error::Transport(_))), "{item:?}");
        stamps.push(Instant::now());
    }
    let gaps: Vec<Duration> = stamps.windows(2).map(|w| w[1] - w[0]).collect();
    for (i, g) in gaps.iter().enumerate() {
        assert!(
            *g <= Duration::from_secs(31),
            "gap {i} = {g:?} exceeds the cap"
        );
    }
    // Early gaps are short, late gaps sit between half the cap and the cap.
    assert!(gaps[0] < Duration::from_secs(2), "{:?}", gaps[0]);
    for g in &gaps[8..] {
        assert!(*g >= Duration::from_secs(14), "{g:?}");
    }
}

#[tokio::test]
async fn os_events_typed_stream() {
    let (server, client) = signed_in().await;
    let body = ":keepalive\n\ndata: {\"kind\":\"events.heartbeat\",\"ts\":1.5}\n\ndata: {\"kind\":\"notification.added\",\"id\":\"tr1\",\"ts\":2.0}\n\ndata: {\"kind\":\"events.lagged\",\"id\":null,\"ts\":3.0,\"dropped\":4}\n\n";
    Mock::given(method("GET"))
        .and(path("/api/os/events"))
        .and(query_param("kinds", "notification.added,decision.answered"))
        .respond_with(sse(body))
        .mount(&server)
        .await;
    let mut s = client.os_events(&["notification.added", "decision.answered"]);
    assert!(s.next().await.expect("item").expect("event").is_heartbeat());
    let n = s.next().await.expect("item").expect("event");
    assert_eq!(
        (n.kind.as_str(), n.id.as_deref()),
        ("notification.added", Some("tr1"))
    );
    let lag = s.next().await.expect("item").expect("event");
    assert!(lag.is_lagged() && lag.dropped == Some(4));
}

#[tokio::test]
async fn event_stream_typed_with_notification_payload() {
    let (server, client) = signed_in().await;
    let frame = r#"{"type":"notification.added","payload":{"id":77,"timestamp":1,"level":"info","title":"Hi","message":"m","read":false,"source":"x"},"ts":5.0,"id":"trace-1"}"#;
    let other = r#"{"type":"worker.join","payload":{"name":"pc"},"ts":6.0,"id":"trace-2"}"#;
    Mock::given(method("GET"))
        .and(path("/api/events/stream"))
        .respond_with(sse(&format!(
            "id: 1\ndata: {frame}\n\nid: 2\ndata: {other}\n\n"
        )))
        .mount(&server)
        .await;
    let mut s = client.event_stream();
    let a = s.next().await.expect("item").expect("event");
    assert_eq!(a.notification().expect("notification row").id, 77);
    assert_eq!(a.id.as_deref(), Some("trace-1"));
    let b = s.next().await.expect("item").expect("event");
    assert!(b.notification().is_none());
    assert_eq!(b.payload["name"], "pc");
    // Per-connection ids are never replayed back to the server.
    drop(s);
    for r in find(&server, "GET", "/api/events/stream").await {
        assert!(header(&r, "last-event-id").is_none());
    }
}

#[test]
fn backoff_values() {
    let b = Backoff::default();
    assert_eq!(b.ceiling(0), Duration::from_millis(500));
    assert_eq!(b.ceiling(5), Duration::from_secs(16));
    assert_eq!(b.ceiling(6), Duration::from_secs(30));
    assert_eq!(b.ceiling(u32::MAX), Duration::from_secs(30));
}

// ---------------------------------------------------------------------------
// PollWhileVisible, on the paused clock
// ---------------------------------------------------------------------------

fn counter() -> (
    Arc<AtomicUsize>,
    impl FnMut() -> std::future::Ready<()> + Send + 'static,
) {
    let n = Arc::new(AtomicUsize::new(0));
    let c = n.clone();
    (n, move || {
        c.fetch_add(1, Ordering::SeqCst);
        std::future::ready(())
    })
}

#[tokio::test(start_paused = true)]
async fn poller_fires_immediately_then_every_period() {
    let (n, f) = counter();
    let p = PollWhileVisible::spawn(Duration::from_secs(2), true, f);
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 1, "immediate first run");
    tokio::time::sleep(Duration::from_secs(2)).await;
    assert_eq!(n.load(Ordering::SeqCst), 2);
    tokio::time::sleep(Duration::from_secs(4)).await;
    assert_eq!(n.load(Ordering::SeqCst), 4);
    p.stop();
    tokio::time::sleep(Duration::from_secs(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 4, "stopped");
}

#[tokio::test(start_paused = true)]
async fn poller_pauses_while_hidden_and_fires_at_once_when_shown() {
    let (n, f) = counter();
    let p = PollWhileVisible::spawn(Duration::from_secs(2), true, f);
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 1);
    p.set_visible(false);
    assert!(!p.is_visible());
    tokio::time::sleep(Duration::from_secs(60)).await;
    assert_eq!(n.load(Ordering::SeqCst), 1, "paused while hidden");
    p.set_visible(true);
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(
        n.load(Ordering::SeqCst),
        2,
        "fires immediately when shown again"
    );
    tokio::time::sleep(Duration::from_secs(2)).await;
    assert_eq!(n.load(Ordering::SeqCst), 3, "then resumes the period");
    p.set_visible(true); // no-op
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 3);
}

#[tokio::test(start_paused = true)]
async fn poller_starting_hidden_waits_for_visibility() {
    let (n, f) = counter();
    let p = PollWhileVisible::spawn(Duration::from_secs(2), false, f);
    tokio::time::sleep(Duration::from_secs(30)).await;
    assert_eq!(n.load(Ordering::SeqCst), 0);
    p.set_visible(true);
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 1);
}

#[tokio::test(start_paused = true)]
async fn poller_hide_and_show_inside_one_period_still_refires() {
    let (n, f) = counter();
    let p = PollWhileVisible::spawn(Duration::from_secs(10), true, f);
    tokio::time::sleep(Duration::from_millis(10)).await;
    p.set_visible(false);
    p.set_visible(true);
    tokio::time::sleep(Duration::from_millis(10)).await;
    assert_eq!(n.load(Ordering::SeqCst), 2);
}

#[tokio::test(start_paused = true)]
async fn poller_manual_poll_and_no_overlap() {
    let n = Arc::new(AtomicUsize::new(0));
    let running = Arc::new(AtomicUsize::new(0));
    let overlap = Arc::new(AtomicUsize::new(0));
    let (nn, rr, oo) = (n.clone(), running.clone(), overlap.clone());
    let p = PollWhileVisible::spawn(Duration::from_secs(1), true, move || {
        let (nn, rr, oo) = (nn.clone(), rr.clone(), oo.clone());
        async move {
            if rr.fetch_add(1, Ordering::SeqCst) > 0 {
                oo.fetch_add(1, Ordering::SeqCst);
            }
            tokio::time::sleep(Duration::from_secs(3)).await; // slower than the period
            nn.fetch_add(1, Ordering::SeqCst);
            rr.fetch_sub(1, Ordering::SeqCst);
        }
    });
    tokio::time::sleep(Duration::from_secs(20)).await;
    assert_eq!(overlap.load(Ordering::SeqCst), 0, "runs never overlap");
    let before = n.load(Ordering::SeqCst);
    assert!(
        (4..=6).contains(&before),
        "about one run per 4 s, got {before}"
    );
    p.poll_now();
    tokio::time::sleep(Duration::from_secs(4)).await;
    assert!(n.load(Ordering::SeqCst) > before);
}

/// A raw server that answers with an SSE header and then stays silent for
/// `quiet` before sending one event, like a file watch on an unchanged folder.
async fn quiet_server(quiet: Duration) -> String {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind");
    let addr = listener.local_addr().expect("addr");
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else {
                return;
            };
            tokio::spawn(async move {
                let mut buf = [0u8; 4096];
                let _ = sock.read(&mut buf).await;
                let head = "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream\r\ntransfer-encoding: chunked\r\n\r\n";
                let _ = sock.write_all(head.as_bytes()).await;
                tokio::time::sleep(quiet).await;
                let _ = sock.write_all(b"9\r\ndata: x\n\n\r\n").await;
                tokio::time::sleep(Duration::from_secs(5)).await;
            });
        }
    });
    format!("http://{addr}")
}

#[tokio::test]
async fn idle_timeout_reconnects_a_silent_stream_unless_disabled() {
    let base = quiet_server(Duration::from_millis(300)).await;
    let client = Client::with_base_url(base).expect("client");
    let mut s = client.sse(
        "/w",
        vec![],
        SseOptions {
            idle_timeout: Some(Duration::from_millis(60)),
            ..fast()
        },
    );
    assert!(
        matches!(s.next().await.expect("item"), Err(Error::Disconnected)),
        "silence past the limit reconnects"
    );
    drop(s);
    // The file watch routes send no keepalive, so they run with no idle timeout.
    let mut s = client.sse(
        "/w",
        vec![],
        SseOptions {
            idle_timeout: None,
            ..fast()
        },
    );
    assert_eq!(s.next().await.expect("item").expect("event").data, "x");
}
