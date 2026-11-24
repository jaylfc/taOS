// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;

use common::*;
use taos_client::agents::{AgentState, Lifecycle};
use taos_client::Error;
use wiremock::matchers::{header_exists, method, path};
use wiremock::{Mock, ResponseTemplate};

async fn mount_list(server: &wiremock::MockServer) {
    get_fixture(server, "/api/agents", "agents_list.json").await;
    get_fixture(server, "/api/agents/containers", "agents_containers.json").await;
    get_fixture(server, "/api/cluster/workers", "agents_workers.json").await;
}

#[tokio::test]
async fn list_merges_containers_and_sorts_by_display_name() {
    let (server, client) = signed_in().await;
    mount_list(&server).await;
    let list = client.agents().list(false).await.expect("list");
    let names: Vec<_> = list.iter().map(|a| a.name.as_str()).collect();
    // display names: Builder, legacy (slug), nostatus (slug), Researcher, scribe (slug)
    assert_eq!(
        names,
        ["builder", "legacy", "nostatus", "researcher", "scribe"]
    );

    let by = |n: &str| list.iter().find(|a| a.name == n).unwrap();
    let r = by("researcher");
    assert_eq!(r.state, AgentState::Running);
    assert_eq!(r.container.as_ref().unwrap().ip, "10.0.0.21");
    assert_eq!(r.actions, ["pause", "restart", "stop"]);
    assert_eq!(r.owner_id, None, "owners are only joined on request");

    let s = by("scribe");
    assert_eq!(s.state, AgentState::Paused);
    assert!(s.frozen);
    assert_eq!(s.display(), "scribe");
    assert_eq!(s.actions, ["resume"]);

    let b = by("builder");
    assert_eq!(b.state, AgentState::Stopped);
    assert_eq!(b.model, "");
    assert_eq!(b.vectors, 17.0);
    assert_eq!(b.container.as_ref().unwrap().ip, "");
    assert_eq!(b.actions, ["start"]);

    // No container row: the stored status decides; a missing status is stopped.
    assert_eq!(by("legacy").state, AgentState::Error);
    assert_eq!(by("nostatus").state, AgentState::Stopped);
    assert_eq!(by("legacy").vectors, 0.0);
}

#[tokio::test]
async fn the_secret_key_field_is_not_modelled() {
    let (server, client) = signed_in().await;
    mount_list(&server).await;
    let list = client.agents().list(false).await.expect("list");
    let dump = serde_json::to_string(&list).unwrap();
    assert!(
        !dump.contains("never-sent") && !dump.contains("llm_key"),
        "{dump}"
    );
}

#[tokio::test]
async fn a_failing_containers_call_does_not_fail_the_list() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/agents", "agents_list.json").await;
    Mock::given(method("GET"))
        .and(path("/api/agents/containers"))
        .respond_with(
            ResponseTemplate::new(500)
                .set_body_raw(r#"{"error":"incus down"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    let list = client.agents().list(false).await.expect("list still works");
    let r = list.iter().find(|a| a.name == "researcher").unwrap();
    assert_eq!(r.state, AgentState::Running, "stored status");
    assert!(r.container.is_none());
    let s = list.iter().find(|a| a.name == "scribe").unwrap();
    assert_eq!(s.state, AgentState::Paused, "the paused flag still shows");
}

#[tokio::test]
async fn owners_are_joined_from_the_registry_for_admins() {
    let (server, client) = signed_in().await;
    mount_list(&server).await;
    get_fixture(&server, "/api/agents/registry", "agents_registry.json").await;
    let list = client.agents().list(true).await.expect("list");
    let owner = |n: &str| list.iter().find(|a| a.name == n).unwrap().owner_id.clone();
    assert_eq!(owner("researcher").as_deref(), Some("u-alex"));
    assert_eq!(owner("scribe").as_deref(), Some("u-amy"));
    assert_eq!(owner("builder"), None, "no registry row for it");
}

#[tokio::test]
async fn a_registry_refusal_leaves_owners_empty_but_lists() {
    let (server, client) = signed_in().await;
    mount_list(&server).await;
    Mock::given(method("GET"))
        .and(path("/api/agents/registry"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"forbidden"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    let list = client.agents().list(true).await.expect("list");
    assert_eq!(list.len(), 5);
    assert!(list.iter().all(|a| a.owner_id.is_none()));
}

#[tokio::test]
async fn list_403_and_401_are_reported() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/agents"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"forbidden"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    match client.agents().list(false).await {
        Err(Error::Forbidden { message, csrf }) => {
            assert_eq!(message, "forbidden");
            assert!(!csrf);
        }
        other => panic!("{other:?}"),
    }
}

#[tokio::test]
async fn get_applies_the_live_state() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/agents/containers", "agents_containers.json").await;
    mount(
        &server,
        "GET",
        "/api/agents/scribe",
        r#"{"name":"scribe","status":"running","paused":false,"framework":"hermes"}"#,
    )
    .await;
    let a = client.agents().get("scribe").await.expect("get");
    assert_eq!(a.state, AgentState::Paused);
    assert!(a.frozen);
}

#[tokio::test]
async fn get_403_and_404_map_with_the_controllers_words() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/agents/theirs"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"forbidden"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/api/agents/ghost"))
        .respond_with(
            ResponseTemplate::new(404)
                .set_body_raw(r#"{"error":"Agent 'ghost' not found"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(matches!(
        client.agents().get("theirs").await,
        Err(Error::Forbidden { .. })
    ));
    match client.agents().get("ghost").await {
        Err(Error::NotFound { message }) => assert_eq!(message, "Agent 'ghost' not found"),
        other => panic!("{other:?}"),
    }
}

#[tokio::test]
async fn lifecycle_posts_to_the_right_route_with_csrf() {
    let (server, client) = signed_in().await;
    for a in ["start", "restart", "pause", "resume"] {
        mount(
            &server,
            "POST",
            &format!("/api/agents/a%20b/{a}"),
            r#"{"success":true}"#,
        )
        .await;
    }
    // the name is one path segment, however odd
    for a in [
        Lifecycle::Start,
        Lifecycle::Restart,
        Lifecycle::Pause,
        Lifecycle::Resume,
    ] {
        let out = client.agents().act("a b", a).await.expect("act");
        assert!(out.ok, "{a:?}");
    }
    let posts = find(&server, "POST", "/api/agents/a%20b/pause").await;
    assert_eq!(posts.len(), 1);
    assert_eq!(header(&posts[0], "x-csrf-token").as_deref(), Some(CSRF));
}

#[tokio::test]
async fn lifecycle_refusals_keep_the_controllers_sentence() {
    let (server, client) = signed_in().await;
    for (verb, status, body) in [
        ("start", 403u16, r#"{"detail":"forbidden"}"#),
        (
            "pause",
            409,
            r#"{"error":"Agent 'x' is not running (container is Stopped)"}"#,
        ),
        (
            "restart",
            500,
            r#"{"error":"Could not restart agent 'x': no space"}"#,
        ),
        ("resume", 404, r#"{"error":"Agent 'x' not found"}"#),
    ] {
        Mock::given(method("POST"))
            .and(path(format!("/api/agents/x/{verb}")))
            .respond_with(ResponseTemplate::new(status).set_body_raw(body, "application/json"))
            .mount(&server)
            .await;
    }
    let a = client.agents();
    assert!(matches!(
        a.act("x", Lifecycle::Start).await,
        Err(Error::Forbidden { .. })
    ));
    match a.act("x", Lifecycle::Pause).await {
        Err(Error::Conflict { message }) => {
            assert!(message.contains("is not running"), "{message}")
        }
        other => panic!("{other:?}"),
    }
    match a.act("x", Lifecycle::Restart).await {
        Err(Error::Server {
            status: 500,
            message,
        }) => assert!(message.contains("no space")),
        other => panic!("{other:?}"),
    }
    assert!(matches!(
        a.act("x", Lifecycle::Resume).await,
        Err(Error::NotFound { .. })
    ));
}

#[tokio::test]
async fn a_stop_that_did_not_stop_is_not_ok() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "POST",
        "/api/agents/x/stop",
        r#"{"prepare_report":{},"stop_result":{"success":false,"output":"device busy"},"force_killed":false,"force_output":""}"#,
    )
    .await;
    mount(
        &server,
        "POST",
        "/api/agents/y/stop",
        r#"{"prepare_report":{},"stop_result":{"success":true,"output":""},"force_killed":false}"#,
    )
    .await;
    let bad = client
        .agents()
        .act("x", Lifecycle::Stop)
        .await
        .expect("200");
    assert!(!bad.ok);
    assert_eq!(bad.message, "device busy");
    assert!(
        client
            .agents()
            .act("y", Lifecycle::Stop)
            .await
            .expect("200")
            .ok
    );
}

#[tokio::test]
async fn taos_agent_config_is_read() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/taos-agent/config", "taos_agent_config.json").await;
    let c = client.agents().taos_agent().await.expect("config");
    assert_eq!(c.model.as_deref(), Some("qwen3-8b"));
    assert_eq!(c.permitted_models, ["qwen3-8b", "gemma-3"]);
    assert_eq!(c.framework, "opencode");
}

#[tokio::test]
async fn the_agent_list_sends_the_session_cookie() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/agents"))
        .and(header_exists("cookie"))
        .respond_with(json_body("[]"))
        .mount(&server)
        .await;
    assert!(client.agents().list(false).await.expect("list").is_empty());
}

#[tokio::test]
async fn each_agent_names_its_device_with_kind_and_reachability() {
    let (server, client) = signed_in().await;
    mount_list(&server).await;
    let list = client.agents().list(false).await.expect("list");
    let dev = |n: &str| list.iter().find(|a| a.name == n).unwrap().device.clone();
    let here = dev("researcher");
    assert!(here.local && here.name.is_empty() && here.reach == "online");
    let phone = dev("scribe");
    assert_eq!(
        (
            phone.name.as_str(),
            phone.kind.as_str(),
            phone.reach.as_str()
        ),
        ("pixel", "phone", "online")
    );
    let off = dev("builder");
    assert_eq!(
        (off.name.as_str(), off.reach.as_str()),
        ("ghost-box", "offline")
    );
}

#[tokio::test]
async fn a_refused_cluster_list_makes_remote_devices_unknown_not_failed() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/agents", "agents_list.json").await;
    Mock::given(method("GET"))
        .and(path("/api/cluster/workers"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"forbidden"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    let list = client.agents().list(false).await.expect("list");
    let d = &list.iter().find(|a| a.name == "scribe").unwrap().device;
    assert_eq!(
        (d.name.as_str(), d.reach.as_str(), d.kind.as_str()),
        ("pixel", "unknown", "")
    );
}
