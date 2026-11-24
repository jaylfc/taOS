//! The named operations behind `taos_call`.
//!
//! Each op takes a JSON object of arguments and returns a JSON value that is
//! the serialised typed result from `taos-client` (so the quirks that crate
//! handles, for example the notification list normalisation, apply). `http`
//! is the raw escape hatch for routes that have no typed wrapper yet.

use serde_json::{json, Value};
use taos_client::agents::Lifecycle;
use taos_client::decisions::{AnswerRequest, DecisionFilter};
use taos_client::notifications::ArchiveOutcome;
use taos_client::{Client, Error, Result};

fn arg_str<'a>(args: &'a Value, key: &str) -> Result<&'a str> {
    args.get(key)
        .and_then(Value::as_str)
        .ok_or_else(|| Error::InvalidInput(format!("missing string argument `{key}`")))
}

fn arg_i64(args: &Value, key: &str) -> Result<i64> {
    args.get(key)
        .and_then(Value::as_i64)
        .ok_or_else(|| Error::InvalidInput(format!("missing integer argument `{key}`")))
}

fn opt_str(args: &Value, key: &str) -> Option<String> {
    args.get(key).and_then(Value::as_str).map(str::to_string)
}

fn to_value<T: serde::Serialize>(v: &T) -> Result<Value> {
    serde_json::to_value(v).map_err(|e| Error::InvalidInput(e.to_string()))
}

fn answer_from(args: &Value) -> Result<AnswerRequest> {
    let other = opt_str(args, "other");
    let mut req = match (args.get("value"), other) {
        (Some(Value::String(s)), _) => AnswerRequest::option(s.clone()),
        (Some(Value::Array(a)), None) => {
            AnswerRequest::options(a.iter().filter_map(Value::as_str).map(str::to_string))
        }
        (Some(Value::Array(a)), Some(o)) => AnswerRequest::options_and_other(
            a.iter().filter_map(Value::as_str).map(str::to_string),
            o,
        ),
        (None, Some(o)) => AnswerRequest::other(o),
        _ => {
            return Err(Error::InvalidInput(
                "answer needs `value` (string or array) and/or `other`".into(),
            ))
        }
    };
    if let Some(n) = opt_str(args, "note") {
        req = req.with_note(n);
    }
    Ok(req)
}

/// Run one operation.
pub async fn run(c: &Client, op: &str, args: Value) -> Result<Value> {
    match op {
        // ----- raw ---------------------------------------------------------
        "http" => {
            let method = arg_str(&args, "method")?;
            let path = arg_str(&args, "path")?;
            let query: Vec<(String, String)> = match args.get("query") {
                Some(Value::Object(m)) => m
                    .iter()
                    .map(|(k, v)| {
                        (
                            k.clone(),
                            v.as_str().map_or_else(|| v.to_string(), str::to_string),
                        )
                    })
                    .collect(),
                _ => Vec::new(),
            };
            c.raw_json(method, path, &query, args.get("body").cloned())
                .await
        }

        // ----- auth --------------------------------------------------------
        "auth.login" => {
            let out = c
                .login(
                    args.get("username").and_then(Value::as_str),
                    arg_str(&args, "password")?,
                    args.get("remember").and_then(Value::as_bool),
                )
                .await?;
            Ok(json!({"user": to_value(&out.user)?, "needs_onboarding": out.needs_onboarding}))
        }
        "auth.pin_login" => {
            let out = c
                .pin_login(
                    args.get("username").and_then(Value::as_str),
                    arg_str(&args, "pin")?,
                )
                .await?;
            Ok(json!({"user": to_value(&out.user)?, "needs_onboarding": out.needs_onboarding}))
        }
        "auth.logout" => {
            c.logout().await?;
            Ok(Value::Null)
        }
        "auth.me" => to_value(&c.me().await?),

        // ----- notifications -----------------------------------------------
        "notifications.list" => {
            let unread = args
                .get("unread_only")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            to_value(&c.notifications().list(unread).await?)
        }
        "notifications.mark_read" => {
            c.notifications().mark_read(arg_i64(&args, "id")?).await?;
            Ok(Value::Null)
        }
        "notifications.mark_all_read" => {
            Ok(json!({"marked": c.notifications().mark_all_read().await?}))
        }
        "notifications.archive" => {
            let o = c.notifications().archive(arg_i64(&args, "id")?).await?;
            Ok(json!({"outcome": match o {
                ArchiveOutcome::Archived => "archived",
                ArchiveOutcome::KeptPendingRequest => "kept_pending_request",
            }}))
        }
        "notifications.clear" => {
            let s = c.notifications().clear().await?;
            Ok(json!({"archived": s.archived, "kept": s.kept}))
        }

        // ----- decisions ---------------------------------------------------
        "decisions.list" => {
            let f = DecisionFilter {
                status: opt_str(&args, "status"),
                project_id: opt_str(&args, "project_id"),
                limit: args.get("limit").and_then(Value::as_u64).map(|n| n as u32),
            };
            to_value(&c.decisions().list(&f).await?)
        }
        "decisions.get" => to_value(&c.decisions().get(arg_str(&args, "id")?).await?),
        "decisions.history" => to_value(&c.decisions().history(arg_str(&args, "id")?).await?),
        "decisions.answer" => {
            let d = c
                .decisions()
                .answer(arg_str(&args, "id")?, &answer_from(&args)?)
                .await?;
            to_value(&d)
        }
        "decisions.add_note" => to_value(
            &c.decisions()
                .add_note(arg_str(&args, "id")?, arg_str(&args, "text")?)
                .await?,
        ),

        // ----- agents ------------------------------------------------------
        "agents.list" => {
            let owners = args
                .get("with_owners")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            to_value(&c.agents().list(owners).await?)
        }
        "agents.get" => to_value(&c.agents().get(arg_str(&args, "name")?).await?),
        "agents.act" => {
            let action = Lifecycle::parse(arg_str(&args, "action")?).ok_or_else(|| {
                Error::InvalidInput("action must be start, stop, restart, pause or resume".into())
            })?;
            to_value(&c.agents().act(arg_str(&args, "name")?, action).await?)
        }
        "agents.taos_agent" => to_value(&c.agents().taos_agent().await?),

        // ----- dashboard ---------------------------------------------------
        "dashboard.activity" => to_value(&c.dashboard().activity().await?),
        "dashboard.loaded_models" => to_value(&c.dashboard().loaded_models().await?),
        "dashboard.scheduler_stats" => to_value(&c.dashboard().scheduler_stats().await?),
        "dashboard.scheduler_tasks" => {
            let limit = args.get("limit").and_then(Value::as_u64).unwrap_or(50) as u32;
            to_value(&c.dashboard().scheduler_tasks(limit).await?)
        }
        "dashboard.workers" => to_value(&c.dashboard().workers().await?),
        "dashboard.snapshot" => {
            let d = c.dashboard();
            let (a, l, s, w) = tokio::try_join!(
                d.activity(),
                d.loaded_models(),
                d.scheduler_stats(),
                d.workers()
            )?;
            Ok(json!({
                "activity": to_value(&a)?,
                "loaded_models": to_value(&l)?,
                "scheduler": to_value(&s)?,
                "workers": to_value(&w)?,
            }))
        }

        // ----- models ------------------------------------------------------
        "models.catalog" => to_value(&c.models().catalog().await?),
        "models.workers" => to_value(&c.models().workers().await?),
        "models.providers" => to_value(&c.models().providers().await?),
        "models.downloads" => to_value(&c.models().downloads().await?),
        "models.start_download" => to_value(
            &c.models()
                .start_download(arg_str(&args, "app_id")?, arg_str(&args, "variant_id")?)
                .await?,
        ),
        "models.download_progress" => to_value(
            &c.models()
                .download_progress(arg_str(&args, "download_id")?)
                .await?,
        ),
        "models.delete" => to_value(&c.models().delete(arg_str(&args, "model_id")?).await?),

        chat if chat.starts_with("chat.") => c.chat().run(chat, args).await,

        other => Err(Error::InvalidInput(format!("unknown operation `{other}`"))),
    }
}
