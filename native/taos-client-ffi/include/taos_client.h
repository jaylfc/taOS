/*
 * taos_client.h: C ABI over the Rust taos-client crate.
 *
 * Hand maintained (cbindgen is not available in the build environment);
 * tests/header.rs fails if a symbol or constant drifts from src/lib.rs.
 * Qt code should not include this directly: use the taosqt library.
 *
 * Model
 *   One callback per client, registered at creation. Requests and streams are
 *   identified by caller-chosen uint64 ids. All strings are UTF-8, NUL
 *   terminated; payloads are JSON. The json pointer given to the callback is
 *   valid only during the call.
 *
 * Threading
 *   The callback runs on a library worker thread, one call at a time, with an
 *   internal lock held. It must copy what it needs, hand it to the UI thread
 *   and return. It must not call any taos_* function (taos_client_free
 *   detects this and refuses; the others would deadlock).
 *
 * Lifetime guarantees
 *   - A request gets exactly one terminal event: TAOS_EV_RESULT or TAOS_EV_FAILED.
 *   - A stream gets any number of TAOS_EV_STREAM_EVENT / TAOS_EV_STREAM_RETRYING
 *     and then at most one TAOS_EV_STREAM_ENDED (fatal error).
 *   - After taos_cancel(id) returns, no callback for id is running or will run.
 *   - After taos_client_free returns, no callback is running or will run, so
 *     the user pointer may be freed.
 */
#ifndef TAOS_CLIENT_H
#define TAOS_CLIENT_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Callback event kinds. */
#define TAOS_EV_RESULT 1          /* request done; json = response value */
#define TAOS_EV_FAILED 2          /* request failed; json = error object */
#define TAOS_EV_STREAM_EVENT 3    /* json = {"event","id","data"} */
#define TAOS_EV_STREAM_RETRYING 4 /* connection lost, reconnecting; json = error object */
#define TAOS_EV_STREAM_ENDED 5    /* stream is over (fatal); json = error object */

/* Return codes of the taos_* functions. */
#define TAOS_OK 0
#define TAOS_ERR_ARGS 1           /* null or malformed argument */
#define TAOS_ERR_ID_IN_USE 2
#define TAOS_ERR_CLOSED 3
#define TAOS_ERR_INTERNAL 4       /* a panic was caught */
#define TAOS_ERR_IN_CALLBACK 5    /* taos_client_free called from a callback */

/*
 * Error object (json of TAOS_EV_FAILED, _RETRYING, _ENDED):
 *   {"kind": "unauthenticated"|"forbidden"|"not_found"|"conflict"|"rate_limited"|
 *            "validation"|"bad_request"|"http"|"server"|"network"|"decode"|
 *            "stream"|"unsupported"|"invalid_input"|"protocol"|"io"|"internal",
 *    "message": "<the controller's own words, or a transport detail>",
 *    "status": 401,                     (when there was an HTTP status)
 *    "retry_after_ms": 1500,            (rate_limited, when known)
 *    "csrf": true,                      (forbidden)
 *    "fields": [{"location","message"}] (validation)}
 */

typedef struct TaosClient TaosClient;

typedef void (*TaosCallback)(void *user, uint64_t id, int kind, const char *json, size_t len);

typedef struct TaosConfig {
    const char *base_url;       /* required */
    const char *session_json;   /* from taos_session_export, or NULL */
    const char *bearer_token;   /* host-local token, or NULL */
    uint32_t request_timeout_ms;   /* 0 = 30000 */
    uint32_t connect_timeout_ms;   /* 0 = 10000 */
    uint32_t sse_backoff_base_ms;  /* 0 = 500 */
    uint32_t sse_backoff_cap_ms;   /* 0 = 30000 */
    uint32_t sse_idle_timeout_ms;  /* 0 = 60000 */
} TaosConfig;

const char *taos_version(void); /* static, do not free */

/* NULL on failure; then *error_json_out (if non-NULL) is an error object to
 * release with taos_string_free. */
TaosClient *taos_client_new(const TaosConfig *cfg, TaosCallback cb, void *user,
                            char **error_json_out);

/* Abort everything and free. See "Lifetime guarantees". */
int taos_client_free(TaosClient *c);

/*
 * Start an operation. op is one of:
 *   http                     {method, path, query?{}, body?}  -> raw JSON
 *   auth.login               {username?, password, remember?} -> {user, needs_onboarding}
 *   auth.pin_login           {username?, pin}
 *   auth.logout | auth.me
 *   notifications.list       {unread_only?}
 *   notifications.mark_read  {id}      notifications.archive {id} -> {outcome}
 *   notifications.mark_all_read -> {marked}   notifications.clear -> {archived, kept}
 *   decisions.list           {status?, project_id?, limit?}
 *   decisions.get | .history {id}
 *   decisions.answer         {id, value?: string|[string], other?, note?}
 *   decisions.add_note       {id, text}
 *   dashboard.activity | .loaded_models | .scheduler_stats | .workers | .snapshot
 *   dashboard.scheduler_tasks {limit?}
 *   models.catalog | .workers | .providers | .downloads
 *   models.start_download    {app_id, variant_id}
 *   models.download_progress {download_id}      models.delete {model_id}
 * The result JSON is the serialised typed result of taos-client.
 */
int taos_call(TaosClient *c, uint64_t id, const char *op, const char *args_json);

/* Reconnecting SSE stream; query_json = {"k":"v"} or NULL. */
int taos_stream_open(TaosClient *c, uint64_t id, const char *path, const char *query_json);

int taos_cancel(TaosClient *c, uint64_t id);

/* Replace or (NULL) remove the bearer token. */
int taos_set_bearer(TaosClient *c, const char *token);
int taos_has_credentials(TaosClient *c);

/* NULL when there is no cookie session. Contains the live token. */
char *taos_session_export(TaosClient *c);
int taos_session_forget(TaosClient *c);

void taos_string_free(char *s);

#ifdef __cplusplus
}
#endif

#endif /* TAOS_CLIENT_H */
