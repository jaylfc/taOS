mod common;

use std::sync::{Arc, Mutex};

use bytes::Bytes;
use common::*;
use futures_util::StreamExt;
use taos_client::files::{Location, Op, UploadSource};
use taos_client::Error;
use wiremock::matchers::{body_json, method, path, query_param};
use wiremock::{Mock, ResponseTemplate};

#[test]
fn location_ids_and_support_matrix() {
    assert_eq!(Location::parse("workspace"), Location::Workspace);
    assert_eq!(Location::parse("agent:bob"), Location::Agent("bob".into()));
    assert_eq!(
        Location::parse("project:alpha"),
        Location::Project("alpha".into())
    );
    assert_eq!(
        Location::parse("photos"),
        Location::SharedFolder("photos".into())
    );
    assert_eq!(Location::Agent("bob".into()).id(), "agent:bob");
    let shared = Location::SharedFolder("p".into());
    assert!(shared.supports(Op::List) && shared.supports(Op::Upload));
    for op in [
        Op::Read,
        Op::Mkdir,
        Op::Delete,
        Op::Rename,
        Op::Copy,
        Op::Watch,
        Op::Stats,
        Op::Trash,
    ] {
        assert!(!shared.supports(op), "{op:?}");
    }
    let agent = Location::Agent("a".into());
    assert!(agent.supports(Op::Delete) && !agent.supports(Op::Rename) && !agent.supports(Op::Copy));
    assert!(Location::Workspace.supports(Op::Rename));
}

#[tokio::test]
async fn locations_combines_three_calls() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/workspace/stats", "stats.json").await;
    get_fixture(&server, "/api/shared-folders", "shared_folders.json").await;
    get_fixture(&server, "/api/agents", "agents.json").await;
    let l = client.files().locations().await.expect("locations");
    assert_eq!(l.workspace.expect("stats").total_files, 42);
    assert_eq!(l.shared_folders[0].name, "photos");
    assert_eq!(l.agents[0].display_name.as_deref(), Some("Agent A"));
    assert_eq!(l.agents[1].name, "agent-b");
    assert!(l.warnings.is_empty());
}

#[tokio::test]
async fn locations_keeps_going_when_one_part_fails_but_not_on_401() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/workspace/stats", "stats.json").await;
    get_fixture(&server, "/api/agents", "agents.json").await;
    Mock::given(method("GET"))
        .and(path("/api/shared-folders"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server)
        .await;
    let l = client.files().locations().await.expect("locations");
    assert!(l.shared_folders.is_empty());
    assert_eq!(l.warnings.len(), 1);

    let (server2, client2) = signed_in().await;
    Mock::given(method("GET"))
        .respond_with(
            ResponseTemplate::new(401)
                .set_body_raw(r#"{"error":"Authentication required"}"#, "application/json"),
        )
        .mount(&server2)
        .await;
    assert!(client2
        .files()
        .locations()
        .await
        .expect_err("401")
        .is_unauthenticated());
}

#[tokio::test]
async fn list_every_location_kind() {
    let (server, client) = signed_in().await;
    for p in [
        "/api/workspace/files",
        "/api/agents/agent-a/workspace/files",
        "/api/projects/alpha/files",
    ] {
        get_fixture(&server, p, "files_list.json").await;
    }
    get_fixture(
        &server,
        "/api/shared-folders/photos/files",
        "shared_files.json",
    )
    .await;
    let f = client.files();
    let ws = f.list(&Location::Workspace, "").await.expect("workspace");
    assert!(ws[0].is_dir && ws[0].size == 0);
    assert_eq!(ws[1].path, "docs/a b.txt");
    assert_eq!(ws[1].size, 11);
    f.list(&Location::Agent("agent-a".into()), "docs")
        .await
        .expect("agent");
    f.list(&Location::Project("alpha".into()), "")
        .await
        .expect("project");
    let shared = f
        .list(&Location::SharedFolder("photos".into()), "")
        .await
        .expect("shared");
    assert_eq!(shared[0].path, "report.pdf", "path defaults to name");
    assert!(!shared[0].is_dir);
    assert_eq!(shared[0].size, 1_572_864, "size_mb is converted");
    // The path parameter is only sent when a directory is given.
    assert!(find(&server, "GET", "/api/workspace/files").await[0]
        .url
        .query()
        .is_none());
    assert_eq!(
        find(&server, "GET", "/api/agents/agent-a/workspace/files").await[0]
            .url
            .query(),
        Some("path=docs")
    );
    assert!(matches!(
        f.list(&Location::SharedFolder("photos".into()), "sub")
            .await,
        Err(Error::Unsupported(_))
    ));
}

#[tokio::test]
async fn download_streams_the_body_and_reads_headers() {
    let (server, client) = signed_in().await;
    let payload: Vec<u8> = (0..200_000u32).map(|i| (i % 251) as u8).collect();
    Mock::given(method("GET"))
        .and(path("/api/workspace/files/docs/a%20b.txt"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_bytes(payload.clone())
                .insert_header("content-type", "text/plain")
                .insert_header("content-disposition", "attachment; filename=\"a b.txt\""),
        )
        .mount(&server)
        .await;
    let mut dl = client
        .files()
        .download(&Location::Workspace, "docs/a b.txt")
        .await
        .expect("download");
    assert_eq!(dl.content_length, Some(200_000));
    assert_eq!(dl.content_type.as_deref(), Some("text/plain"));
    assert_eq!(dl.file_name.as_deref(), Some("a b.txt"));
    let mut got = Vec::new();
    let mut chunks = 0;
    while let Some(c) = dl.next().await {
        got.extend_from_slice(&c.expect("chunk"));
        chunks += 1;
    }
    assert_eq!(got, payload);
    assert!(chunks >= 1);

    // And through the helpers.
    let bytes = client
        .files()
        .download(&Location::Workspace, "docs/a b.txt")
        .await
        .expect("download")
        .to_bytes()
        .await
        .expect("bytes");
    assert_eq!(bytes.len(), 200_000);
    let out = std::env::temp_dir().join(format!("taos-client-dl-{}.bin", std::process::id()));
    let n = client
        .files()
        .download(&Location::Workspace, "docs/a b.txt")
        .await
        .expect("download")
        .save_to(&out)
        .await
        .expect("save");
    assert_eq!(n, 200_000);
    assert_eq!(std::fs::read(&out).expect("read back"), payload);
    std::fs::remove_file(&out).expect("cleanup");
}

#[tokio::test]
async fn download_errors_and_unsupported_locations_send_nothing() {
    let (server, client) = signed_in().await;
    Mock::given(method("GET"))
        .and(path("/api/workspace/files/missing.txt"))
        .respond_with(
            ResponseTemplate::new(404)
                .set_body_raw(r#"{"error":"'missing.txt' not found"}"#, "application/json"),
        )
        .mount(&server)
        .await;
    assert!(matches!(
        client
            .files()
            .download(&Location::Workspace, "missing.txt")
            .await,
        Err(Error::NotFound { .. })
    ));
    let before = received(&server).await.len();
    let f = client.files();
    let shared = Location::SharedFolder("photos".into());
    assert!(matches!(
        f.download(&shared, "x").await,
        Err(Error::Unsupported(_))
    ));
    assert!(matches!(
        f.mkdir(&shared, "x").await,
        Err(Error::Unsupported(_))
    ));
    assert!(matches!(
        f.delete(&shared, "x").await,
        Err(Error::Unsupported(_))
    ));
    assert!(matches!(f.watch(&shared, ""), Err(Error::Unsupported(_))));
    assert!(matches!(
        f.rename(&Location::Agent("a".into()), "x", "y").await,
        Err(Error::Unsupported(_))
    ));
    assert!(matches!(
        f.copy(&Location::Project("p".into()), "x", "y").await,
        Err(Error::Unsupported(_))
    ));
    assert!(matches!(
        f.download(&Location::Workspace, "../etc/passwd").await,
        Err(Error::InvalidInput(_))
    ));
    assert_eq!(received(&server).await.len(), before, "nothing was sent");
}

#[tokio::test]
async fn upload_bytes_with_progress_and_csrf() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/workspace/files/upload"))
        .and(query_param("path", "docs"))
        .respond_with(json_body(
            r#"{"name":"hello.txt","path":"docs/hello.txt","size":150000,"status":"uploaded"}"#,
        ))
        .mount(&server)
        .await;
    let data = Bytes::from(vec![b'z'; 150_000]);
    let seen = Arc::new(Mutex::new(Vec::new()));
    let sink = seen.clone();
    let up = client
        .files()
        .upload(
            &Location::Workspace,
            "docs",
            UploadSource::Bytes {
                file_name: "hello.txt".into(),
                data,
            },
            move |p| sink.lock().expect("lock").push((p.sent, p.total)),
        )
        .await
        .expect("upload");
    assert_eq!(up.path.as_deref(), Some("docs/hello.txt"));
    assert_eq!(up.size, 150_000);
    let seen = seen.lock().expect("lock").clone();
    assert!(seen.len() >= 3, "progress called per chunk: {seen:?}");
    assert_eq!(*seen.last().expect("last"), (150_000, 150_000));
    assert!(seen.windows(2).all(|w| w[0].0 < w[1].0));

    let r = &find(&server, "POST", "/api/workspace/files/upload").await[0];
    assert_eq!(header(r, "x-csrf-token").as_deref(), Some(CSRF));
    assert!(header(r, "content-type")
        .expect("ct")
        .starts_with("multipart/form-data"));
    let body = String::from_utf8_lossy(&r.body);
    assert!(body.contains("name=\"file\"") && body.contains("filename=\"hello.txt\""));
    assert!(body.contains(&"z".repeat(1000)));
}

#[tokio::test]
async fn upload_a_file_from_disk_to_a_shared_folder() {
    let (server, client) = signed_in().await;
    mount(
        &server,
        "POST",
        "/api/shared-folders/photos/upload",
        r#"{"status":"uploaded","name":"disk.bin","size":10}"#,
    )
    .await;
    let p = std::env::temp_dir().join(format!("taos-client-up-{}.bin", std::process::id()));
    std::fs::write(&p, b"0123456789").expect("write");
    let up = client
        .files()
        .upload(
            &Location::SharedFolder("photos".into()),
            "",
            UploadSource::File(p.clone()),
            |_| {},
        )
        .await
        .expect("upload");
    std::fs::remove_file(&p).expect("cleanup");
    assert_eq!(up.name, "disk.bin");
    let r = &find(&server, "POST", "/api/shared-folders/photos/upload").await[0];
    assert!(String::from_utf8_lossy(&r.body).contains("0123456789"));
    assert!(r.url.query().is_none());
}

#[tokio::test]
async fn mkdir_rename_copy_delete_and_stats() {
    let (server, client) = signed_in().await;
    Mock::given(method("POST"))
        .and(path("/api/agents/agent-a/workspace/mkdir"))
        .and(body_json(serde_json::json!({"path": "new dir"})))
        .respond_with(json_body(r#"{"path":"new dir","status":"created"}"#))
        .mount(&server)
        .await;
    let f = client.files();
    assert_eq!(
        f.mkdir(&Location::Agent("agent-a".into()), "new dir")
            .await
            .expect("mkdir")
            .status,
        "created"
    );
    Mock::given(method("POST"))
        .and(path("/api/workspace/rename"))
        .and(body_json(serde_json::json!({"src": "a", "dst": "b"})))
        .respond_with(json_body(r#"{"path":"b","status":"renamed"}"#))
        .mount(&server)
        .await;
    assert_eq!(
        f.rename(&Location::Workspace, "a", "b")
            .await
            .expect("rename")
            .path
            .as_deref(),
        Some("b")
    );
    mount(
        &server,
        "POST",
        "/api/workspace/copy",
        r#"{"path":"c","status":"copied"}"#,
    )
    .await;
    assert_eq!(
        f.copy(&Location::Workspace, "a", "c")
            .await
            .expect("copy")
            .status,
        "copied"
    );
    mount(
        &server,
        "DELETE",
        "/api/projects/alpha/files/x/y%20z.md",
        r#"{"path":"x/y z.md","status":"deleted"}"#,
    )
    .await;
    assert_eq!(
        f.delete(&Location::Project("alpha".into()), "x/y z.md")
            .await
            .expect("delete")
            .status,
        "deleted"
    );
    Mock::given(method("POST"))
        .and(path("/api/workspace/rename"))
        .respond_with(
            ResponseTemplate::new(409)
                .set_body_raw(r#"{"error":"Target already exists"}"#, "application/json"),
        )
        .with_priority(10)
        .mount(&server)
        .await;
    get_fixture(&server, "/api/projects/alpha/stats", "stats.json").await;
    assert_eq!(
        f.stats(&Location::Project("alpha".into()))
            .await
            .expect("stats")
            .total_size,
        123_456
    );
    let del = &find(&server, "DELETE", "/api/projects/alpha/files/x/y%20z.md").await[0];
    assert_eq!(header(del, "x-csrf-token").as_deref(), Some(CSRF));
}

#[tokio::test]
async fn trash_restore_purge_and_container_recycle() {
    let (server, client) = signed_in().await;
    get_fixture(&server, "/api/workspace/trash", "trash.json").await;
    get_fixture(&server, "/api/agents/agent-a/workspace/trash", "trash.json").await;
    let f = client.files();
    let items = f.trash(&Location::Workspace).await.expect("trash");
    assert_eq!(items[0].original_path, "docs/old.txt");
    assert_eq!(
        items[0].deleted_at.as_ref().and_then(|t| t.epoch_secs()),
        Some(1_794_650_400.0)
    );
    assert_eq!(
        f.trash(&Location::Agent("agent-a".into()))
            .await
            .expect("agent trash")
            .len(),
        1
    );

    mount(
        &server,
        "POST",
        "/api/workspace/trash/0a1b2c/restore",
        r#"{"status":"restored","path":"docs/old.txt"}"#,
    )
    .await;
    assert_eq!(
        f.restore(&Location::Workspace, "0a1b2c")
            .await
            .expect("restore")
            .path
            .as_deref(),
        Some("docs/old.txt")
    );
    mount(
        &server,
        "DELETE",
        "/api/workspace/trash/0a1b2c",
        r#"{"status":"purged","id":"0a1b2c"}"#,
    )
    .await;
    f.purge(&Location::Workspace, "0a1b2c")
        .await
        .expect("purge");
    mount(
        &server,
        "DELETE",
        "/api/workspace/trash",
        r#"{"status":"emptied","count":2}"#,
    )
    .await;
    assert_eq!(f.empty_trash(&Location::Workspace).await.expect("empty"), 2);
    Mock::given(method("POST"))
        .and(path("/api/projects/alpha/trash/zz/restore"))
        .respond_with(ResponseTemplate::new(409).set_body_raw(
            r#"{"error":"a file already exists at the original path"}"#,
            "application/json",
        ))
        .mount(&server)
        .await;
    assert!(matches!(
        f.restore(&Location::Project("alpha".into()), "zz").await,
        Err(Error::Conflict { .. })
    ));

    get_fixture(&server, "/api/recycle", "recycle_all.json").await;
    assert_eq!(
        f.agent_recycle().await.expect("recycle")[0]
            .agent_name
            .as_deref(),
        Some("agent-a")
    );
    get_fixture(&server, "/api/agents/agent-a/recycle", "recycle_agent.json").await;
    assert!(f
        .agent_recycle_for("agent-a")
        .await
        .expect("per agent")
        .container_offline());
    Mock::given(method("POST"))
        .and(path("/api/agents/agent-a/recycle/restore"))
        .and(body_json(serde_json::json!({"id": "L3RtcC94"})))
        .respond_with(json_body(
            r#"{"status":"restored","original_path":"/tmp/x"}"#,
        ))
        .mount(&server)
        .await;
    f.restore_agent_recycle("agent-a", "L3RtcC94")
        .await
        .expect("restore container item");
    mount(
        &server,
        "DELETE",
        "/api/agents/agent-a/recycle/L3RtcC94",
        r#"{"status":"purged"}"#,
    )
    .await;
    f.purge_agent_recycle("agent-a", "L3RtcC94")
        .await
        .expect("purge container item");
}

#[tokio::test]
async fn watch_yields_listings_and_ends_on_an_error_frame() {
    let (server, client) = signed_in().await;
    let body = format!(
        ": connected\n\ndata: {}\n\nevent: error\ndata: {{\"error\": \"Directory not found\"}}\n\n",
        fixture("files_list.json").replace('\n', "")
    );
    Mock::given(method("GET"))
        .and(path("/api/workspace/files/watch"))
        .and(query_param("path", "docs"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(body, "text/event-stream"))
        .mount(&server)
        .await;
    let mut s = client
        .files()
        .watch(&Location::Workspace, "docs")
        .expect("watch");
    let first = s.next().await.expect("item").expect("listing");
    assert_eq!(first.len(), 2);
    assert_eq!(first[0].name, "docs");
    match s.next().await.expect("item") {
        Err(Error::Stream(text)) => assert!(text.contains("Directory not found")),
        other => panic!("{other:?}"),
    }
    assert!(s.next().await.is_none(), "an error frame ends the stream");
    tokio::time::sleep(std::time::Duration::from_millis(900)).await;
    assert_eq!(
        find(&server, "GET", "/api/workspace/files/watch")
            .await
            .len(),
        1,
        "no reconnect after an error frame"
    );
}
