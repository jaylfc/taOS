mod common;

use common::*;
use taos_client::chat::SendOutcome;
use taos_client::Error;
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

#[tokio::test]
async fn channels_unread_and_agents() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/chat/channels", "chat_channels.json").await;
    mount(&server, "GET", "/api/chat/unread", r#"{"unread":{"ch2":3,"ch1":0,"ch3":1}}"#).await;
    mount(&server, "GET", "/api/agents", r#"[{"name":"alpha","display_name":"Alpha","status":"running","x":1}]"#).await;
    mount(&server, "GET", "/api/agents/archived", r#"[{"id":"a1","archived_slug":"old-1","original":{"name":"old"}}]"#).await;
    let chat = client.chat();
    let ch = chat.channels(false, None).await.unwrap();
    assert_eq!(ch.len(), 5);
    assert_eq!(chat.unread().await.unwrap(), vec![("ch2".into(), 3), ("ch3".into(), 1)]);
    assert_eq!(chat.agents(true).await.unwrap()[0].status.as_deref(), Some("running"));
    assert_eq!(chat.archived_agents().await.unwrap()[0].archived_slug, "old-1");
    let r = &find(&server, "GET", "/api/agents").await[0];
    assert_eq!(r.url.query(), Some("include_guests=1"));
}

#[tokio::test]
async fn messages_paginate_with_before_and_limit() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/chat/channels/ch2/messages"))
        .and(query_param("limit", "2"))
        .and(query_param("before", "1760000100.5"))
        .respond_with(json_body(r#"{"messages":[{"id":"a","created_at":1,"channel_id":"ch2"},{"id":"b","created_at":2,"channel_id":"ch2"}]}"#))
        .mount(&server)
        .await;
    get_fixture(&server, "/api/chat/channels/ch2/messages", "chat_messages.json").await;
    let page = client.chat().messages("ch2", 2, Some(1760000100.5)).await.unwrap();
    assert_eq!(page.iter().map(|m| m.id.as_str()).collect::<Vec<_>>(), ["a", "b"]);
}

#[tokio::test]
async fn send_posts_the_exact_body_and_understands_help() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/chat/messages"))
        .and(body_json(serde_json::json!({
            "channel_id":"ch2","author_id":"user","author_type":"user","content":"hi","content_type":"text"})))
        .respond_with(json_body(r#"{"id":"n1","channel_id":"ch2","author_id":"u1","author_type":"user","content":"hi","created_at":5}"#))
        .mount(&server)
        .await;
    match client.chat().send("ch2", "hi", None).await.unwrap() {
        SendOutcome::Sent(m) => assert_eq!(m.id, "n1"),
        other => panic!("{other:?}"),
    }
    let (server, client) = signed_in().await;
    mount(&server, "POST", "/api/chat/messages",
        r#"{"ok":true,"handled":"help","system_message":{"id":"s1","channel_id":"ch2","author_id":"system","author_type":"system","content":"help text","created_at":6}}"#).await;
    match client.chat().send("ch2", "/help", None).await.unwrap() {
        SendOutcome::Handled { handled, system_message } => {
            assert_eq!(handled, "help");
            assert_eq!(system_message.unwrap().author_type, "system");
        }
        other => panic!("{other:?}"),
    }
}

#[tokio::test]
async fn send_failure_carries_the_servers_words() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/chat/messages"))
        .respond_with(ResponseTemplate::new(400).set_body_raw(r#"{"error":"address a member first"}"#, "application/json"))
        .mount(&server)
        .await;
    let e = client.chat().send("ch2", "/clear", None).await.unwrap_err();
    assert!(matches!(e, Error::BadRequest { .. }), "{e:?}");
    assert!(e.to_string().contains("address a member first"), "{e}");
}

#[tokio::test]
async fn mark_read_and_typing_and_named_ops() {
    let (server, client) = signed_in().await;
    mount(&server, "POST", "/api/chat/channels/ch2/mark-read", r#"{"status":"marked"}"#).await;
    mount(&server, "POST", "/api/chat/channels/ch2/typing", r#"{"ok":true}"#).await;
    mount(&server, "GET", "/api/chat/unread", r#"{"unread":{"ch2":1}}"#).await;
    client.chat().mark_read("ch2", None).await.unwrap();
    client.chat().typing("ch2", "user").await.unwrap();
    let v = client.chat().run("chat.unread", serde_json::json!({})).await.unwrap();
    assert_eq!(v["ch2"], 1);
    let e = client.chat().run("chat.messages", serde_json::json!({})).await.unwrap_err();
    assert!(matches!(e, Error::InvalidInput(_)));
    let e = client.chat().run("chat.nope", serde_json::json!({})).await.unwrap_err();
    assert!(matches!(e, Error::InvalidInput(_)));
    assert_eq!(find(&server, "POST", "/api/chat/channels/ch2/mark-read").await.len(), 1);
}

#[tokio::test]
async fn unauthenticated_is_reported_as_such() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/chat/channels"))
        .respond_with(ResponseTemplate::new(401).set_body_raw(r#"{"error":"nope"}"#, "application/json"))
        .mount(&server)
        .await;
    assert!(client.chat().channels(false, None).await.unwrap_err().is_unauthenticated());
}
