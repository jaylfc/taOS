//! Manual smoke test against a real controller.
//!
//! ```text
//! TAOS_URL=http://127.0.0.1:6969 TAOS_USER=alex TAOS_PASS=... cargo run --example smoke
//! ```
//!
//! Signs in, then prints the signed-in user, the unread notification count and
//! up to three file locations. Nothing is written anywhere.

use taos_client::Client;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let url =
        std::env::var("TAOS_URL").unwrap_or_else(|_| taos_client::DEFAULT_BASE_URL.to_string());
    let user = std::env::var("TAOS_USER").ok();
    let pass = std::env::var("TAOS_PASS")
        .map_err(|_| "set TAOS_PASS (and TAOS_USER on multi-user controllers)")?;

    let client = Client::with_base_url(url)?;
    let outcome = client.login(user.as_deref(), &pass, Some(false)).await?;
    println!(
        "signed in as {} (admin: {})",
        outcome.user.username, outcome.user.is_admin
    );

    let me = client.me().await?;
    println!("me: {me:?}");

    let unread = client.notifications().list(true).await?;
    println!("unread notifications: {}", unread.len());

    let locations = client.files().locations().await?;
    let mut ids = vec!["workspace".to_string()];
    ids.extend(locations.shared_folders.iter().map(|f| f.name.clone()));
    ids.extend(locations.agents.iter().map(|a| format!("agent:{}", a.name)));
    for id in ids.into_iter().take(3) {
        println!("location: {id}");
    }
    for w in &locations.warnings {
        println!("warning: {w}");
    }

    client.logout().await?;
    Ok(())
}
