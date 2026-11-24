// SPDX-License-Identifier: AGPL-3.0-or-later
mod common;

use common::*;
use taos_client::decisions::{
    AnswerRequest, DecisionFilter, DecisionStatus, DecisionType, Priority,
};
use taos_client::Error;
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

#[tokio::test]
async fn list_with_filters_and_tolerant_parsing() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/decisions"))
        .and(query_param("status", "pending"))
        .and(query_param("project_id", "prj_1"))
        .and(query_param("limit", "50"))
        .respond_with(json_fixture("decisions_list.json"))
        .mount(&server)
        .await;
    let items = client
        .decisions()
        .list(&DecisionFilter {
            status: Some("pending".into()),
            project_id: Some("prj_1".into()),
            limit: Some(50),
        })
        .await
        .expect("list");
    assert_eq!(items.len(), 3);
    let d = &items[0];
    assert_eq!(d.kind, DecisionType::SingleSelect);
    assert_eq!(d.priority, Priority::Blocking);
    assert_eq!(d.status, DecisionStatus::Pending);
    assert!(d.options[0].recommended);
    assert_eq!(d.notes[0].text, "think about backups");
    assert_eq!(
        d.created_at.as_ref().and_then(|t| t.epoch_secs()),
        Some(1_760_000_000.25)
    );

    let answered = &items[1];
    assert_eq!(answered.status, DecisionStatus::Answered);
    assert_eq!(
        answered.answer.as_ref().and_then(|a| a.value.as_str()),
        Some("approve")
    );
    assert_eq!(
        answered.answer.as_ref().and_then(|a| a.note.as_deref()),
        Some("go")
    );
    // ISO created_at is normalised to epoch seconds.
    assert_eq!(
        answered.created_at.as_ref().and_then(|t| t.epoch_secs()),
        Some(1_794_650_400.0)
    );
    assert_eq!(answered.metadata["kind"], "execution_gate");

    // Unknown enum values survive; unknown fields are ignored.
    let odd = &items[2];
    assert_eq!(odd.kind, DecisionType::Unknown("ranking".into()));
    assert_eq!(odd.kind.as_str(), "ranking");
    assert_eq!(odd.priority, Priority::Unknown("someday".into()));
    assert_eq!(odd.status, DecisionStatus::Unknown("teleported".into()));
}

#[tokio::test]
async fn list_accepts_a_bare_array() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/decisions"))
        .respond_with(json_body(
            r#"[{"id":"x","question":"q","type":"free_text","status":"pending"}]"#,
        ))
        .mount(&server)
        .await;
    let items = client
        .decisions()
        .list(&DecisionFilter::default())
        .await
        .expect("list");
    assert_eq!(items[0].id, "x");
    // No filter means no query string.
    assert!(find(&server, "GET", "/api/decisions").await[0]
        .url
        .query()
        .is_none());
}

#[tokio::test]
async fn get_and_history() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "GET",
        "/api/decisions/dec_1",
        r#"{"id":"dec_1","question":"q","type":"single_select","status":"pending"}"#,
    )
    .await;
    mount(
        &server,
        "GET",
        "/api/decisions/dec_2/history",
        r#"{"items":[{"id":"dec_0"},{"id":"dec_2"}]}"#,
    )
    .await;
    assert_eq!(
        client.decisions().get("dec_1").await.expect("get").id,
        "dec_1"
    );
    let chain = client.decisions().history("dec_2").await.expect("history");
    assert_eq!(
        chain.iter().map(|d| d.id.as_str()).collect::<Vec<_>>(),
        ["dec_0", "dec_2"]
    );
}

#[tokio::test]
async fn the_four_answer_forms_and_other() {
    let (server, client) = signed_in().await;
    let ok = json_fixture("decision_answered.json");
    for id in ["a", "b", "c", "d", "e", "f"] {
        Mock::given(method("POST"))
            .and(path(format!("/api/decisions/{id}/answer")))
            .respond_with(ok.clone())
            .mount(&server)
            .await;
    }
    let d = client.decisions();
    let answered = d
        .answer("a", &AnswerRequest::option("pg").with_note("because"))
        .await
        .expect("single");
    assert_eq!(answered.status, DecisionStatus::Answered);
    d.answer("b", &AnswerRequest::options(["x", "y"]))
        .await
        .expect("multi");
    d.answer("c", &AnswerRequest::approve())
        .await
        .expect("approve");
    d.answer("d", &AnswerRequest::text("free words"))
        .await
        .expect("free text");
    d.answer("e", &AnswerRequest::other("something else"))
        .await
        .expect("other single");
    d.answer("f", &AnswerRequest::options_and_other(["x"], "and more"))
        .await
        .expect("other multi");

    let reqs = received(&server).await;
    let body = |id: &str| {
        let want = format!("/api/decisions/{id}/answer");
        let r = reqs
            .iter()
            .find(|r| r.url.path() == want)
            .expect("request sent");
        serde_json::from_str::<serde_json::Value>(&body_text(r)).expect("json")
    };
    assert_eq!(
        body("a"),
        serde_json::json!({"value": "pg", "note": "because"})
    );
    assert_eq!(body("b"), serde_json::json!({"value": ["x", "y"]}));
    assert_eq!(body("c"), serde_json::json!({"value": "approve"}));
    assert_eq!(body("d"), serde_json::json!({"value": "free words"}));
    assert_eq!(
        body("e"),
        serde_json::json!({"value": "", "other_value": "something else"})
    );
    assert_eq!(
        body("f"),
        serde_json::json!({"value": ["x"], "other_value": "and more"})
    );
}

#[tokio::test]
async fn answer_errors_surface_the_servers_error_text() {
    let (server, client) = signed_in().await;
    for (id, status, text) in [
        ("p", 409, "already answered or not pending"),
        ("q", 400, "answer is not one of the options"),
        ("r", 404, "not found"),
    ] {
        Mock::given(method("POST"))
            .and(path(format!("/api/decisions/{id}/answer")))
            .respond_with(
                ResponseTemplate::new(status)
                    .set_body_raw(format!(r#"{{"error":"{text}"}}"#), "application/json"),
            )
            .mount(&server)
            .await;
    }
    let d = client.decisions();
    assert!(
        matches!(d.answer("p", &AnswerRequest::approve()).await, Err(Error::Conflict { message }) if message == "already answered or not pending")
    );
    assert!(
        matches!(d.answer("q", &AnswerRequest::option("zz")).await, Err(Error::BadRequest { message }) if message == "answer is not one of the options")
    );
    assert!(matches!(
        d.answer("r", &AnswerRequest::approve()).await,
        Err(Error::NotFound { .. })
    ));
}

#[tokio::test]
async fn answer_and_note_limits_are_checked_before_sending() {
    let (server, client) = signed_in().await;
    let d = client.decisions();
    let long = "x".repeat(20_001);
    assert!(matches!(
        d.answer("a", &AnswerRequest::text(long)).await,
        Err(Error::InvalidInput(_))
    ));
    assert!(matches!(
        d.answer("a", &AnswerRequest::approve().with_note("n".repeat(501)))
            .await,
        Err(Error::InvalidInput(_))
    ));
    assert!(matches!(
        d.add_note("a", &"n".repeat(501)).await,
        Err(Error::InvalidInput(_))
    ));
    assert_eq!(received(&server).await.len(), 1, "only the login was sent");
}

#[tokio::test]
async fn note() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/decisions/dec_1/note"))
        .and(body_json(serde_json::json!({"text": "hello"})))
        .respond_with(json_fixture("decision_answered.json"))
        .mount(&server)
        .await;
    client
        .decisions()
        .add_note("dec_1", "hello")
        .await
        .expect("note");
    Mock::given(method("POST"))
        .and(path("/api/decisions/dec_2/note"))
        .respond_with(
            ResponseTemplate::new(400)
                .set_body_raw(r#"{"error":"text must not be empty"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(
        matches!(client.decisions().add_note("dec_2", "  ").await, Err(Error::BadRequest { message }) if message == "text must not be empty")
    );
}

#[tokio::test]
async fn access_and_scope_requests() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/agents/auth-requests"))
        .and(query_param("status", "pending"))
        .respond_with(json_fixture("access_requests.json"))
        .mount(&server)
        .await;
    Mock::given(method("GET"))
        .and(path("/api/agents/scope-requests"))
        .and(query_param("status", "pending"))
        .respond_with(json_fixture("scope_requests.json"))
        .mount(&server)
        .await;
    let a = client.decisions().access_requests().await.expect("access");
    assert_eq!(a[0].requested_scopes, ["files_read", "project_tasks"]);
    assert_eq!(
        a[0].human_duration.as_deref(),
        Some("expires 2 hours after approval")
    );
    let s = client.decisions().scope_requests().await.expect("scope");
    assert_eq!(s[0].agent_display_name.as_deref(), Some("Agent A"));
    assert_eq!(s[0].project_id.as_deref(), Some("prj_1"));
}

#[tokio::test]
async fn non_admin_gets_forbidden_for_access_requests() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/agents/auth-requests"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"forbidden"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(matches!(
        client.decisions().access_requests().await,
        Err(Error::Forbidden { csrf: false, .. })
    ));
}

#[tokio::test]
async fn a_malformed_record_is_an_error_with_a_path_not_a_silent_drop() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "GET",
        "/api/decisions",
        r#"{"items":[{"id":"ok","question":"q"},{"id":"bad","question":5}]}"#,
    )
    .await;
    match client
        .decisions()
        .list(&DecisionFilter::default())
        .await
        .expect_err("decode")
    {
        Error::Decode { path, .. } => assert_eq!(path, "items[1].question"),
        other => panic!("{other:?}"),
    }
    // A null or object where a list is normal still reads as empty.
    mount(
        &server,
        "GET",
        "/api/agents/scope-requests",
        r#"{"requests": null}"#,
    )
    .await;
    assert!(client
        .decisions()
        .scope_requests()
        .await
        .expect("null list")
        .is_empty());
}
