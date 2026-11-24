mod common;

use common::*;
use taos_client::{Client, Error, Session, USER_AGENT};
use wiremock::matchers::{header as header_is, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn login_stores_cookies_and_csrf_only_on_writes() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/auth/me", "auth_me.json").await;
    mount(
        &server,
        "POST",
        "/api/notifications/5/read",
        r#"{"ok":true}"#,
    )
    .await;

    assert!(client.is_signed_in());
    let me = client.me().await.expect("me");
    assert_eq!(me.username, "alex");
    assert!(
        me.capabilities.is_empty(),
        "null capabilities read as empty"
    );
    client
        .notifications()
        .mark_read(5)
        .await
        .expect("mark read");

    let me_req = &find(&server, "GET", "/auth/me").await[0];
    assert!(
        header(me_req, "x-csrf-token").is_none(),
        "GET must not carry the CSRF header"
    );
    assert!(header(me_req, "cookie")
        .expect("cookie")
        .contains(&format!("taos_session={SESSION}")));
    let post = &find(&server, "POST", "/api/notifications/5/read").await[0];
    assert_eq!(header(post, "x-csrf-token").as_deref(), Some(CSRF));
    assert!(header(post, "cookie")
        .expect("cookie")
        .contains(&format!("csrf_token={CSRF}")));
    // The login itself is sent without a console header.
    let login = &find(&server, "POST", "/auth/login").await[0];
    assert!(header(login, "x-taos-console").is_none());
}

#[tokio::test]
async fn login_body_and_user() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .and(wiremock::matchers::body_json(
            serde_json::json!({"username": "alex", "password": PASSWORD, "auto_login": true}),
        ))
        .and(header_is("content-type", "application/json"))
        .respond_with(login_response())
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    let out = client
        .login(Some("alex"), PASSWORD, Some(true))
        .await
        .expect("login");
    assert!(out.user.is_admin);
    assert!(!out.needs_onboarding);
    assert_eq!(
        out.user.created_at.as_ref().and_then(|t| t.epoch_secs()),
        Some(1_767_323_045.0)
    );
}

#[tokio::test]
async fn login_without_cookie_is_a_protocol_error() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(json_fixture("auth_login.json"))
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    let err = client.login(None, "x", None).await.expect_err("no cookie");
    assert!(matches!(err, Error::Protocol(_)), "{err:?}");
}

#[tokio::test]
async fn fixed_user_agent_on_every_request() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/auth/me", "auth_me.json").await;
    client.me().await.expect("me");
    assert_eq!(
        USER_AGENT,
        concat!("taOS-native/", env!("CARGO_PKG_VERSION"))
    );
    for r in received(&server).await {
        assert_eq!(header(&r, "user-agent").as_deref(), Some(USER_AGENT));
    }
}

#[tokio::test]
async fn pin_login_needs_no_console_header() {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/pin-login"))
        .respond_with(login_response())
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    client.pin_login(None, "1234").await.expect("pin login");
    let r = &find(&server, "POST", "/auth/pin-login").await[0];
    // `/auth/pin-login` only checks the peer address (`is_console_origin`);
    // the `X-taOS-Console` header belongs to the other `/auth/lock-*` posts.
    assert!(header(r, "x-taos-console").is_none());
    assert_eq!(
        header(r, "content-type").as_deref(),
        Some("application/json")
    );
    assert_eq!(body_text(r), r#"{"pin":"1234"}"#);
}

#[tokio::test]
async fn logout_follows_the_303_as_success_and_clears_the_session() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/auth/logout"))
        .respond_with(
            ResponseTemplate::new(303)
                .insert_header("location", "/auth/login")
                .append_header(
                    "set-cookie",
                    "taos_session=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; Path=/",
                ),
        )
        .mount(&server)
        .await;
    client.logout().await.expect("logout");
    assert!(!client.is_signed_in());
    assert!(client.export_session().is_none());
    let r = &find(&server, "POST", "/auth/logout").await[0];
    assert_eq!(header(r, "x-csrf-token").as_deref(), Some(CSRF));
}

#[tokio::test]
async fn session_export_round_trips_and_keeps_the_user_agent() {
    let (server, client) = signed_in().await;
    let session = client.export_session().expect("session");
    let stored = serde_json::to_string(&session).expect("serialise");
    // An app would keep `stored` in the keyring. Pretend it came from an older
    // crate version by rewriting the user agent inside it.
    let stored = stored.replace(USER_AGENT, "taOS-native/0.0.1");
    let restored: Session = serde_json::from_str(&stored).expect("deserialise");

    get_fixture(&server, "/auth/me", "auth_me.json").await;
    let client2 = Client::with_session(restored).expect("restore");
    assert_eq!(client2.base_url(), server.uri());
    assert_eq!(client2.user_agent(), "taOS-native/0.0.1");
    client2.me().await.expect("me");
    let r = &find(&server, "GET", "/auth/me").await[0];
    assert_eq!(
        header(r, "user-agent").as_deref(),
        Some("taOS-native/0.0.1")
    );
    assert!(header(r, "cookie").expect("cookie").contains(SESSION));
}

#[tokio::test]
async fn debug_output_never_contains_secrets() {
    let (_server, client) = signed_in().await;
    let session = client.export_session().expect("session");
    let dump = format!("{client:?} {session:?} {:#?} {:#?}", client, session);
    for secret in [SESSION, CSRF, PASSWORD, "csrfnonce0123"] {
        assert!(!dump.contains(secret), "Debug leaked {secret}: {dump}");
    }
    assert!(dump.contains("<redacted>"));
}

#[tokio::test]
async fn csrf_rejection_is_retried_once_with_the_reissued_cookie() {
    let (server, client) = signed_in().await;
    let fresh = "freshnonce.freshmac";
    // The stale token is rejected and the server re-issues a bound cookie.
    Mock::given(method("POST"))
        .and(path("/api/notifications/read-all"))
        .and(header_is("x-csrf-token", CSRF))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(
                    r#"{"detail":"CSRF token not bound to session"}"#,
                    "application/json",
                )
                .append_header(
                    "set-cookie",
                    format!("csrf_token={fresh}; Path=/; SameSite=strict"),
                ),
        )
        .with_priority(1)
        .mount(&server)
        .await;
    Mock::given(method("POST"))
        .and(path("/api/notifications/read-all"))
        .and(header_is("x-csrf-token", fresh))
        .respond_with(json_body(r#"{"ok":true,"marked":3}"#))
        .mount(&server)
        .await;
    let marked = client
        .notifications()
        .mark_all_read()
        .await
        .expect("retry succeeds");
    assert_eq!(marked, 3);
    assert_eq!(
        find(&server, "POST", "/api/notifications/read-all")
            .await
            .len(),
        2
    );
}

#[tokio::test]
async fn a_second_csrf_rejection_surfaces_as_forbidden_csrf() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/notifications/read-all"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"CSRF token mismatch"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    let err = client
        .notifications()
        .mark_all_read()
        .await
        .expect_err("403");
    assert!(
        matches!(err, Error::Forbidden { csrf: true, .. }),
        "{err:?}"
    );
    assert_eq!(
        find(&server, "POST", "/api/notifications/read-all")
            .await
            .len(),
        2,
        "exactly one retry"
    );
}

async fn status_error(status: u16, body: &str, extra: Option<(&str, &str)>) -> Error {
    let server = MockServer::start().await;
    let mut t = ResponseTemplate::new(status).set_body_raw(body.to_string(), "application/json");
    if let Some((k, v)) = extra {
        t = t.insert_header(k, v);
    }
    Mock::given(method("GET"))
        .and(path("/auth/me"))
        .respond_with(t)
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    client.me().await.expect_err("error status")
}

#[tokio::test]
async fn error_mapping() {
    let e = status_error(401, r#"{"error":"Authentication required"}"#, None).await;
    assert!(
        e.is_unauthenticated()
            && matches!(&e, Error::Unauthenticated { message } if message == "Authentication required")
    );
    assert_eq!(e.status(), Some(401));

    let e = status_error(403, r#"{"detail":"forbidden"}"#, None).await;
    assert!(matches!(e, Error::Forbidden { csrf: false, .. }));
    let e = status_error(403, r#"{"detail":"CSRF token missing"}"#, None).await;
    assert!(matches!(e, Error::Forbidden { csrf: true, .. }));

    let e = status_error(404, r#"{"error":"nope"}"#, None).await;
    assert!(matches!(e, Error::NotFound { ref message } if message == "nope"));

    let e = status_error(409, r#"{"error":"already answered or not pending"}"#, None).await;
    assert!(
        matches!(e, Error::Conflict { ref message } if message == "already answered or not pending")
    );

    let e = status_error(
        429,
        r#"{"error":"Too many attempts","retry_after":9}"#,
        Some(("retry-after", "30")),
    )
    .await;
    match e {
        Error::RateLimited {
            retry_after,
            message,
        } => {
            assert_eq!(
                retry_after,
                Some(std::time::Duration::from_secs(30)),
                "header wins"
            );
            assert_eq!(message, "Too many attempts");
        }
        other => panic!("{other:?}"),
    }
    let e = status_error(429, r#"{"error":"slow down","retry_after":7}"#, None).await;
    assert!(matches!(e, Error::RateLimited { retry_after: Some(d), .. } if d.as_secs() == 7));

    let e = status_error(
        422,
        r#"{"detail":[{"loc":["body","granted_scopes"],"msg":"field required","type":"missing"}]}"#,
        None,
    )
    .await;
    match e {
        Error::Validation { fields, message } => {
            assert_eq!(fields.len(), 1);
            assert_eq!(fields[0].location, "body.granted_scopes");
            assert_eq!(fields[0].message, "field required");
            assert!(message.contains("granted_scopes"));
        }
        other => panic!("{other:?}"),
    }

    let e = status_error(400, r#"{"error":"text must not be empty"}"#, None).await;
    assert!(matches!(e, Error::BadRequest { ref message } if message == "text must not be empty"));
    let e = status_error(410, r#"{"detail":"sign-in request expired"}"#, None).await;
    assert!(matches!(e, Error::Http { status: 410, .. }));
    let e = status_error(503, r#"{"detail":"Service starting"}"#, None).await;
    assert!(
        matches!(e, Error::Server { status: 503, ref message } if message == "Service starting")
    );
    let e = status_error(502, "<html><body>Bad gateway</body></html>", None).await;
    assert!(
        matches!(e, Error::Server { status: 502, ref message } if message == "HTTP 502"),
        "{e:?}"
    );
    assert!(e.is_retryable());
    let e = status_error(500, r#"{"error":{"message":"gateway says no"}}"#, None).await;
    assert!(matches!(e, Error::Server { ref message, .. } if message == "gateway says no"));
}

#[tokio::test]
async fn decode_error_reports_path_and_truncated_body() {
    let server = MockServer::start().await;
    let long = "x".repeat(1000);
    let body = format!(r#"{{"user": {{"id": 5, "pad": "{long}"}}}}"#);
    Mock::given(method("GET"))
        .and(path("/auth/me"))
        .respond_with(json_body(&body))
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    match client.me().await.expect_err("decode") {
        Error::Decode { path, body, .. } => {
            assert_eq!(path, "user.id");
            assert!(
                body.len() < 400 && body.ends_with("..."),
                "truncated: {}",
                body.len()
            );
        }
        other => panic!("{other:?}"),
    }
}

#[tokio::test]
async fn transport_error_when_nothing_listens() {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").expect("bind");
    let port = listener.local_addr().expect("addr").port();
    drop(listener);
    let client = Client::with_base_url(format!("http://127.0.0.1:{port}")).expect("client");
    assert!(matches!(
        client.me().await.expect_err("refused"),
        Error::Transport(_)
    ));
}

#[tokio::test]
async fn rejects_bad_base_urls() {
    assert!(matches!(
        Client::with_base_url("not a url"),
        Err(Error::InvalidInput(_))
    ));
    assert!(matches!(
        Client::with_base_url("ftp://x"),
        Err(Error::InvalidInput(_))
    ));
}
