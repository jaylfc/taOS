//! Shared helpers for the integration tests: a fake controller on wiremock.
#![allow(dead_code)]

use std::path::PathBuf;

use taos_client::Client;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, Request, ResponseTemplate};

pub const SESSION: &str = "SESSTOKEN123abc";
pub const CSRF: &str = "csrfnonce0123.mac4567";
pub const PASSWORD: &str = "hunter2-secret-pw";

pub fn fixture(name: &str) -> String {
    let p: PathBuf = [env!("CARGO_MANIFEST_DIR"), "tests", "fixtures", name]
        .iter()
        .collect();
    std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("fixture {name}: {e}"))
}

pub fn json_fixture(name: &str) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_raw(fixture(name), "application/json")
}

pub fn json_body(body: &str) -> ResponseTemplate {
    ResponseTemplate::new(200).set_body_raw(body.to_string(), "application/json")
}

/// A response that sets the session and CSRF cookies like `POST /auth/login`.
pub fn login_response() -> ResponseTemplate {
    json_fixture("auth_login.json")
        .append_header(
            "set-cookie",
            format!("taos_session={SESSION}; HttpOnly; Path=/; SameSite=strict; Max-Age=2592000"),
        )
        .append_header(
            "set-cookie",
            format!("csrf_token={CSRF}; Path=/; SameSite=strict"),
        )
}

/// Mount a GET fixture.
pub async fn get_fixture(server: &MockServer, route: &str, fixture_name: &str) {
    Mock::given(method("GET"))
        .and(path(route))
        .respond_with(json_fixture(fixture_name))
        .mount(server)
        .await;
}

/// Mount a POST/DELETE/etc. with a fixed JSON body.
pub async fn mount(server: &MockServer, verb: &str, route: &str, body: &str) {
    Mock::given(method(verb))
        .and(path(route))
        .respond_with(json_body(body))
        .mount(server)
        .await;
}

/// A fake controller and a client already signed in against it.
pub async fn signed_in() -> (MockServer, Client) {
    let server = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/auth/login"))
        .respond_with(login_response())
        .mount(&server)
        .await;
    let client = Client::with_base_url(server.uri()).expect("client");
    client
        .login(Some("jay"), PASSWORD, Some(true))
        .await
        .expect("login");
    (server, client)
}

pub async fn received(server: &MockServer) -> Vec<Request> {
    server.received_requests().await.unwrap_or_default()
}

pub fn header(req: &Request, name: &str) -> Option<String> {
    req.headers
        .get(name)
        .and_then(|v| v.to_str().ok())
        .map(str::to_string)
}

pub fn body_text(req: &Request) -> String {
    String::from_utf8_lossy(&req.body).into_owned()
}

/// The requests (after the login) matching a method and path.
pub async fn find(server: &MockServer, verb: &str, route: &str) -> Vec<Request> {
    received(server)
        .await
        .into_iter()
        .filter(|r| r.method.as_str() == verb && r.url.path() == route)
        .collect()
}
