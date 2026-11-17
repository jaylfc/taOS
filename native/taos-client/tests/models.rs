mod common;

use std::time::Duration;

use common::*;
use futures_util::StreamExt;
use taos_client::models::{Compatibility, DownloadStatus, DownloadUpdate, Liveness, WorkerState};
use taos_client::Error;
use wiremock::matchers::{body_json, method, path};
use wiremock::{Mock, ResponseTemplate};

#[tokio::test]
async fn catalog_with_unknown_values_and_default_variant() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/models", "models_catalog.json").await;
    let c = client.models().catalog().await.expect("catalog");
    assert_eq!(c.hardware_profile_id.as_deref(), Some("arm64-cpu-16gb"));
    assert_eq!(c.downloaded_files[0].model_id.as_deref(), Some("qwen-7b"));
    assert_eq!(c.models[0].compatibility, Compatibility::Yellow);
    assert_eq!(
        c.models[0].default_variant().expect("variant").id,
        "q2",
        "smallest size wins"
    );
    assert_eq!(
        c.models[1].compatibility,
        Compatibility::Unknown("purple".into())
    );
    assert_eq!(
        c.models[1].default_variant().expect("first").id,
        "v",
        "no sizes: first variant"
    );
    assert!(c.models[1].description.is_none());
}

#[tokio::test]
async fn workers_bare_or_wrapped_and_liveness() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/cluster/workers", "workers.json").await;
    let w = client.models().workers().await.expect("workers");
    assert_eq!(w[0].status, WorkerState::Online);
    assert_eq!(w[0].backends[0].models[1].size_mb, Some(4200.0));
    assert_eq!(w[0].backends[0].models[0].id, "llama3");
    assert_eq!(w[0].backends[0].loaded_models.len(), 1);
    assert_eq!(w[1].status, WorkerState::Unknown("mystery-state".into()));
    assert!(w[1].backends.is_empty(), "null backends");
    assert_eq!(w[0].liveness(1030.0), Liveness::Online);
    assert_eq!(w[0].liveness(1100.0), Liveness::Stale);
    assert_eq!(w[0].liveness(1300.0), Liveness::Offline);
    assert_eq!(w[1].liveness(5.0), Liveness::Unknown);
    assert_eq!(w[0].host_label(), "pc");
    assert_eq!(
        w[1].host_label(),
        "nas.lan",
        "generic name falls back to the url host"
    );

    let (server2, client2) = signed_in().await;
    mount(
        &server2,
        "GET",
        "/api/cluster/workers",
        r#"{"workers":[{"name":"w"}]}"#,
    )
    .await;
    assert_eq!(
        client2.models().workers().await.expect("wrapped")[0].name,
        "w"
    );
}

#[tokio::test]
async fn providers_never_expose_the_api_key() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/providers", "providers.json").await;
    let p = client.models().providers().await.expect("providers");
    assert_eq!(p[0].kind, "openai");
    assert_eq!(p[0].models[0].id, "gpt-x");
    assert_eq!(p[0].models[1].id, "plain-id");
    assert!(!format!("{p:?}").contains("sk-must-never"));
}

#[tokio::test]
async fn download_start_progress_list_and_delete() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/models/download"))
        .and(body_json(
            serde_json::json!({"app_id": "qwen-7b", "variant_id": "q2"}),
        ))
        .respond_with(json_fixture("download_start.json"))
        .mount(&server)
        .await;
    get_fixture(
        &server,
        "/api/models/downloads/qwen-7b-q2",
        "download_progress.json",
    )
    .await;
    get_fixture(&server, "/api/models/downloads", "downloads_all.json").await;
    Mock::given(method("DELETE"))
        .and(path("/api/models/qwen-7b"))
        .respond_with(json_fixture("model_deleted.json"))
        .mount(&server)
        .await;
    let m = client.models();
    let start = m.start_download("qwen-7b", "q2").await.expect("start");
    assert_eq!(start.download_id, "qwen-7b-q2");
    let p = m
        .download_progress(&start.download_id)
        .await
        .expect("progress");
    assert_eq!(
        (p.status.clone(), p.percent),
        (DownloadStatus::Downloading, 25.0)
    );
    let all = m.downloads().await.expect("downloads");
    assert_eq!(all.len(), 2);
    assert_eq!(all[1].status, DownloadStatus::Unknown("refined".into()));
    assert_eq!(
        m.delete("qwen-7b").await.expect("delete").deleted_files,
        ["qwen-7b-q4.gguf"]
    );
    let post = &find(&server, "POST", "/api/models/download").await[0];
    assert_eq!(header(post, "x-csrf-token").as_deref(), Some(CSRF));
}

#[tokio::test]
async fn members_are_forbidden_and_missing_models_are_not_found() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/models/download"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"Admin access required"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    Mock::given(method("DELETE"))
        .and(path("/api/models/ghost"))
        .respond_with(
            ResponseTemplate::new(404)
                .set_body_raw(r#"{"error":"Model 'ghost' not found"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(matches!(
        client.models().start_download("a", "b").await,
        Err(Error::Forbidden { csrf: false, .. })
    ));
    assert!(
        matches!(client.models().delete("ghost").await, Err(Error::NotFound { message }) if message.contains("ghost"))
    );
}

#[tokio::test]
async fn watch_download_polls_sequentially_until_complete() {
    let (server, client) = signed_in().await;
    // First two polls report progress, the third completes.
    Mock::given(method("GET"))
        .and(path("/api/models/downloads/d1"))
        .respond_with(json_fixture("download_progress.json"))
        .up_to_n_times(2)
        .with_priority(1)
        .mount(&server)
        .await;
    get_fixture(
        &server,
        "/api/models/downloads/d1",
        "download_complete.json",
    )
    .await;
    let mut w = client
        .models()
        .watch_download("d1", Duration::from_millis(5));
    let mut kinds = Vec::new();
    while let Some(u) = w.next().await {
        kinds.push(match u {
            DownloadUpdate::Progress(_) => "progress",
            DownloadUpdate::Complete(p) => {
                assert_eq!(p.percent, 100.0);
                "complete"
            }
            other => panic!("{other:?}"),
        });
    }
    assert_eq!(kinds, ["progress", "progress", "complete"]);
}

#[tokio::test]
async fn watch_download_reports_failure_text_and_lost_contact() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "GET",
        "/api/models/downloads/bad",
        r#"{"id":"bad","status":"error","error":""}"#,
    )
    .await;
    let mut w = client
        .models()
        .watch_download("bad", Duration::from_millis(5));
    assert_eq!(
        w.next().await,
        Some(DownloadUpdate::Failed("Download failed".into()))
    );
    assert!(w.next().await.is_none());

    Mock::given(method("GET"))
        .and(path("/api/models/downloads/gone"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server)
        .await;
    let mut w = client
        .models()
        .watch_download("gone", Duration::from_millis(5));
    assert_eq!(w.next().await, Some(DownloadUpdate::LostContact));
    assert!(w.next().await.is_none());
    assert_eq!(
        find(&server, "GET", "/api/models/downloads/gone")
            .await
            .len(),
        3,
        "three strikes"
    );
}

#[tokio::test]
async fn cancelling_a_watch_stops_polling() {
    let (server, client) = signed_in().await;
    get_fixture(
        &server,
        "/api/models/downloads/d2",
        "download_progress.json",
    )
    .await;
    let mut w = client
        .models()
        .watch_download("d2", Duration::from_millis(10));
    assert!(matches!(w.next().await, Some(DownloadUpdate::Progress(_))));
    w.cancel();
    tokio::time::sleep(Duration::from_millis(60)).await;
    let n = find(&server, "GET", "/api/models/downloads/d2").await.len();
    tokio::time::sleep(Duration::from_millis(80)).await;
    assert_eq!(
        find(&server, "GET", "/api/models/downloads/d2").await.len(),
        n
    );
}

#[tokio::test]
async fn a_providers_decode_error_never_quotes_the_body() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "GET",
        "/api/providers",
        r#"[{"name": 5, "api_key": "sk-secret-key-value"}]"#,
    )
    .await;
    let err = client.models().providers().await.expect_err("decode");
    match &err {
        Error::Decode { path, body, .. } => {
            assert_eq!(path, "[0].name");
            assert_eq!(body, "<body withheld>");
        }
        other => panic!("{other:?}"),
    }
    assert!(!format!("{err} {err:?}").contains("sk-secret"));
}
