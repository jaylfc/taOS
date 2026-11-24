mod common;

use common::*;
use taos_client::notifications::{
    AccessApproval, ApproveAccess, ApproveScopes, ArchiveOutcome, Level, Notification,
};
use taos_client::Error;
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

#[tokio::test]
async fn list_parses_fixture_with_unknown_level_and_extra_fields() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/notifications", "notifications_list.json").await;
    let list = client.notifications().list(false).await.expect("list");
    assert_eq!(list.len(), 3);
    assert_eq!(list[0].level, Level::Warning);
    assert!(list[0].is_pinned_grant());
    assert_eq!(list[0].request_id().as_deref(), Some("req_9"));
    assert_eq!(list[1].store_id(), "srv-11");
    assert!(list[1].read && !list[1].is_pinned_grant());
    assert_eq!(list[2].level, Level::Unknown("catastrophic".into()));
    assert_eq!(list[2].level.normalized(), Level::Info);
    // No query string and no HTML switch header.
    let r = &find(&server, "GET", "/api/notifications").await[0];
    assert!(r.url.query().is_none());
    assert!(header(r, "hx-request").is_none());
}

#[tokio::test]
async fn list_unread_only_and_non_array_is_empty() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/notifications"))
        .and(query_param("unread_only", "true"))
        .respond_with(json_body(r#"{"unexpected": "object"}"#))
        .mount(&server)
        .await;
    assert!(client
        .notifications()
        .list(true)
        .await
        .expect("list")
        .is_empty());
}

#[tokio::test]
async fn list_html_body_is_empty() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/notifications"))
        .respond_with(
            ResponseTemplate::new(200).set_body_raw("<div>No notifications</div>", "text/html"),
        )
        .mount(&server)
        .await;
    assert!(client
        .notifications()
        .list(false)
        .await
        .expect("list")
        .is_empty());
}

#[tokio::test]
async fn read_archive_read_all() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "POST",
        "/api/notifications/11/read",
        r#"{"ok":true}"#,
    )
    .await;
    mount(
        &server,
        "POST",
        "/api/notifications/11/archive",
        r#"{"ok":true}"#,
    )
    .await;
    mount(
        &server,
        "POST",
        "/api/notifications/12/archive",
        r#"{"ok":true,"kept":"pending-request"}"#,
    )
    .await;
    mount(
        &server,
        "POST",
        "/api/notifications/read-all",
        r#"{"ok":true,"marked":4}"#,
    )
    .await;
    let n = client.notifications();
    n.mark_read(11).await.expect("read");
    assert_eq!(
        n.archive(11).await.expect("archive"),
        ArchiveOutcome::Archived
    );
    assert_eq!(
        n.archive(12).await.expect("archive"),
        ArchiveOutcome::KeptPendingRequest
    );
    assert_eq!(n.mark_all_read().await.expect("read all"), 4);
    Mock::given(method("POST"))
        .and(path("/api/notifications/99/read"))
        .respond_with(
            ResponseTemplate::new(404)
                .set_body_raw(r#"{"detail":"notification not found"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(matches!(n.mark_read(99).await, Err(Error::NotFound { .. })));
}

#[tokio::test]
async fn clear_archives_everything_except_pending_grant_rows() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/notifications", "notifications_list.json").await;
    mount(
        &server,
        "POST",
        "/api/notifications/11/archive",
        r#"{"ok":true}"#,
    )
    .await;
    mount(
        &server,
        "POST",
        "/api/notifications/10/archive",
        r#"{"ok":true}"#,
    )
    .await;
    let summary = client.notifications().clear().await.expect("clear");
    assert_eq!((summary.archived, summary.kept), (2, 1));
    assert!(find(&server, "POST", "/api/notifications/12/archive")
        .await
        .is_empty());
}

#[tokio::test]
async fn consent_actions() {
    let (server, client) = signed_in().await;
    let n = client.notifications();

    get_fixture(
        &server,
        "/api/agents/scope-vocabulary",
        "scope_vocabulary.json",
    )
    .await;
    let vocab = n.scope_vocabulary().await.expect("vocab");
    assert_eq!(vocab.project_scopes, ["files_write", "project_tasks"]);
    assert_eq!(vocab.valid_scopes.len(), 3);

    Mock::given(method("GET"))
        .and(path("/api/projects"))
        .and(query_param("status", "active"))
        .respond_with(json_fixture("projects_active.json"))
        .mount(&server)
        .await;
    let projects = n.active_projects().await.expect("projects");
    assert_eq!(projects[0].slug, "alpha");

    Mock::given(method("POST"))
        .and(path("/api/projects"))
        .and(body_json(
            serde_json::json!({"name": "Beta", "slug": "beta"}),
        ))
        .respond_with(json_body(r#"{"id":"prj_2","name":"Beta","slug":"beta"}"#))
        .mount(&server)
        .await;
    assert_eq!(
        n.create_project("Beta", "beta").await.expect("create").id,
        "prj_2"
    );

    Mock::given(method("POST"))
        .and(path("/api/agents/auth-requests/req_9/approve"))
        .and(body_json(serde_json::json!({"granted_scopes": ["files_read"], "project_id": "prj_1", "is_lead": true})))
        .respond_with(json_fixture("access_approval.json"))
        .mount(&server)
        .await;
    let approval: AccessApproval = n
        .approve_access_request(
            "req_9",
            &ApproveAccess {
                granted_scopes: vec!["files_read".into()],
                project_id: Some("prj_1".into()),
                is_lead: true,
            },
        )
        .await
        .expect("approve");
    assert_eq!(approval.canonical_id, "agent-new");
    assert!(
        approval.lead_unconfirmed(true),
        "lead_assigned=false is the lead-unconfirmed state"
    );
    assert!(!approval.lead_unconfirmed(false));

    mount(
        &server,
        "POST",
        "/api/agents/auth-requests/req_9/deny",
        r#"{"status":"refused"}"#,
    )
    .await;
    n.deny_access_request("req_9").await.expect("deny");
    assert_eq!(
        body_text(&find(&server, "POST", "/api/agents/auth-requests/req_9/deny").await[0]),
        ""
    );

    Mock::given(method("POST"))
        .and(path(
            "/api/agents/registry/agent-a/scope-requests/sr_1/approve",
        ))
        .and(body_json(
            serde_json::json!({"granted_scopes": ["files_write"], "project_id": "prj_1"}),
        ))
        .respond_with(json_body(
            r#"{"status":"accepted","canonical_id":"agent-a","granted_scopes":["files_write"]}"#,
        ))
        .mount(&server)
        .await;
    let sa = n
        .approve_scope_request(
            "agent-a",
            "sr_1",
            &ApproveScopes {
                granted_scopes: vec!["files_write".into()],
                project_id: Some("prj_1".into()),
            },
        )
        .await
        .expect("approve scope");
    assert_eq!(sa.granted_scopes, ["files_write"]);

    Mock::given(method("POST"))
        .and(path(
            "/api/agents/registry/agent-a/scope-requests/sr_1/deny",
        ))
        .and(body_json(serde_json::json!({"reason": "not now"})))
        .respond_with(json_body(r#"{"status":"refused"}"#))
        .mount(&server)
        .await;
    n.deny_scope_request("agent-a", "sr_1", Some("not now"))
        .await
        .expect("deny scope");
}

#[tokio::test]
async fn sign_in_code_and_its_error_statuses() {
    let (server, client) = signed_in().await;
    let n = client.notifications();
    Mock::given(method("POST"))
        .and(path("/api/auth-broker/requests/r1/code"))
        .and(body_json(serde_json::json!({"code": "123456"})))
        .respond_with(json_body(r#"{"ok":true}"#))
        .mount(&server)
        .await;
    n.submit_sign_in_code("r1", "123456").await.expect("code");
    for (id, status, detail) in [
        ("r2", 410, "sign-in request expired"),
        ("r3", 409, "a code was already given for this request"),
    ] {
        Mock::given(method("POST"))
            .and(path(format!("/api/auth-broker/requests/{id}/code")))
            .respond_with(
                ResponseTemplate::new(status)
                    .set_body_raw(format!(r#"{{"detail":"{detail}"}}"#), "application/json"),
            )
            .mount(&server)
            .await;
    }
    assert!(matches!(
        n.submit_sign_in_code("r2", "1").await,
        Err(Error::Http { status: 410, .. })
    ));
    assert!(matches!(
        n.submit_sign_in_code("r3", "1").await,
        Err(Error::Conflict { .. })
    ));
}

#[test]
fn notification_model_tolerates_missing_everything() {
    let n: Notification = serde_json::from_str("{}").expect("empty object");
    assert_eq!(n.id, 0);
    let n: Notification =
        serde_json::from_str(r#"{"id":1,"level":"info","extra":{"a":[1]}}"#).expect("extra");
    assert_eq!(n.level, Level::Info);
}
