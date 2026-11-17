mod common;

use common::*;
use futures_util::StreamExt;
use taos_client::dashboard::{ModelActivityFilter, TaskStatus};
use taos_client::Error;
use wiremock::matchers::{method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

#[tokio::test]
async fn activity_tolerates_error_path_shapes_and_extra_fields() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/activity", "activity.json").await;
    let a = client.dashboard().activity().await.expect("activity");
    assert_eq!(a.hardware.board.as_deref(), Some("rk3588"));
    assert_eq!(a.hardware.cpu.as_ref().and_then(|c| c.cores), Some(8.0));
    assert_eq!(a.cpu.cores.len(), 2);
    assert_eq!(a.cpu.cores[0].freq_khz, Some(1_800_000.0));
    assert_eq!(a.cpu.cores[1].freq_khz, None);
    assert_eq!(a.cpu.load_avg, [0.5, 0.4, 0.3]);
    assert!(a.npu.cores.is_empty(), "null npu cores");
    assert_eq!(a.npu.kind.as_deref(), Some("rknpu"));
    assert!(
        a.zram.is_empty() && a.network.is_empty(),
        "`{{}}` where a list is normal"
    );
    assert_eq!(
        a.gpu.load.as_ref().and_then(|l| l.load_percent),
        None,
        "empty gpu load object"
    );
    assert_eq!(a.disk.io_rate.write_bps, 2048.0);
    assert_eq!(a.processes[0].rss_mb, 12.0, "numeric strings are accepted");
    assert_eq!(a.thermal[0].temp_c, 48.5);
}

#[tokio::test]
async fn loaded_models_scheduler_and_tasks() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/models/loaded", "loaded_models.json").await;
    get_fixture(&server, "/api/scheduler/stats", "scheduler_stats.json").await;
    Mock::given(method("GET"))
        .and(path("/api/scheduler/tasks"))
        .and(query_param("limit", "8"))
        .respond_with(json_fixture("scheduler_tasks.json"))
        .mount(&server)
        .await;
    let d = client.dashboard();
    let loaded = d.loaded_models().await.expect("loaded");
    assert_eq!(loaded[0].purpose.as_deref(), Some("chat"));
    assert_eq!(loaded[1].size_mb, None);
    let stats = d.scheduler_stats().await.expect("stats");
    assert_eq!(stats.errors, 1.0);
    assert_eq!(stats.resources[0].capabilities, ["chat"]);
    let tasks = d.scheduler_tasks(8).await.expect("tasks");
    assert_eq!(tasks[0].status, TaskStatus::Complete);
    assert_eq!(tasks[1].status, TaskStatus::Unknown("levitating".into()));
}

#[tokio::test]
async fn scheduler_not_ready_is_a_server_error() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/scheduler/stats"))
        .respond_with(ResponseTemplate::new(503).set_body_raw(
            r#"{"error":"resource scheduler not initialised"}"#,
            "application/json",
        ))
        .mount(&server)
        .await;
    assert!(matches!(
        client.dashboard().scheduler_stats().await,
        Err(Error::Server { status: 503, .. })
    ));
}

#[tokio::test]
async fn workers_through_the_dashboard() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/cluster/workers", "workers.json").await;
    assert_eq!(
        client.dashboard().workers().await.expect("workers").len(),
        2
    );
}

#[tokio::test]
async fn model_activity_history_with_filters() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/activity/models"))
        .and(query_param("worker", "controller"))
        .and(query_param("event", "model.load"))
        .and(query_param("limit", "100"))
        .respond_with(json_fixture("model_activity.json"))
        .mount(&server)
        .await;
    let page = client
        .dashboard()
        .model_activity(&ModelActivityFilter {
            worker: Some("controller".into()),
            event: Some("model.load".into()),
            limit: Some(100),
            ..Default::default()
        })
        .await
        .expect("history");
    assert_eq!(page.events[0].seq, 5);
    assert_eq!(page.events[0].duration_ms, Some(1200.0));
    assert_eq!(
        page.events[1].event, "model.teleport",
        "unknown event types are kept"
    );
    assert_eq!(page.event_types.len(), 7);
}

#[tokio::test]
async fn unauthenticated_model_activity_drops_to_lock_screen() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/activity/models"))
        .respond_with(
            ResponseTemplate::new(401)
                .set_body_raw(r#"{"error":"Authentication required"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(client
        .dashboard()
        .model_activity(&ModelActivityFilter::default())
        .await
        .expect_err("401")
        .is_unauthenticated());
}

#[tokio::test]
async fn model_activity_stream_yields_events_with_ids() {
    let (server, client) = signed_in().await;
    let body = ":keepalive\n\nid: 6\ndata: {\"seq\":6,\"ts\":2.0,\"event\":\"request.finish\",\"model\":\"m\",\"worker\":\"controller\",\"tokens_in\":3,\"tokens_out\":9,\"token_rate\":41.5,\"detail\":{}}\n\nid: 7\ndata: {not json}\n\nid: 8\ndata: {\"seq\":8,\"event\":\"model.route\"}\n\n";
    Mock::given(method("GET"))
        .and(path("/api/activity/models/stream"))
        .and(query_param("limit", "0"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(body, "text/event-stream"))
        .mount(&server)
        .await;
    let mut s = client
        .dashboard()
        .model_activity_stream(&ModelActivityFilter {
            limit: Some(0),
            ..Default::default()
        });
    let first = s.next().await.expect("item").expect("event");
    assert_eq!((first.seq, first.token_rate), (6, Some(41.5)));
    assert!(
        matches!(s.next().await.expect("item"), Err(Error::Decode { .. })),
        "a bad frame is reported, not fatal"
    );
    assert_eq!(s.next().await.expect("item").expect("event").seq, 8);
}

#[tokio::test]
async fn restart_ai_stack_and_non_admin_403() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "POST",
        "/api/system/ai-stack/restart",
        &fixture("ai_restart.json"),
    )
    .await;
    let r = client
        .dashboard()
        .restart_ai_stack()
        .await
        .expect("restart");
    assert_eq!(r.status, "partial");
    assert_eq!(r.failed[0].detail.as_deref(), Some("timeout"));
    assert_eq!(r.restarted, ["rkllama.service"]);
    assert_eq!(
        header(
            &find(&server, "POST", "/api/system/ai-stack/restart").await[0],
            "x-csrf-token"
        )
        .as_deref(),
        Some(CSRF)
    );

    Mock::given(method("POST"))
        .and(path("/api/system/ai-stack/restart"))
        .respond_with(
            ResponseTemplate::new(403)
                .set_body_raw(r#"{"detail":"Admin required"}"#, "application/json"),
        )
        .with_priority(1)
        .mount(&server)
        .await;
    assert!(matches!(
        client.dashboard().restart_ai_stack().await,
        Err(Error::Forbidden { csrf: false, .. })
    ));
}
