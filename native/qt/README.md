# taosqt: the foundation for native taOS apps (Qt 6, QML + C++)

Every native app talks to the controller through this layer. Layers:

```text
QML / C++ app
  taosqt (C++, QObject)      native/qt/taosqt        Session, Client, Reply, EventStream, LiveQuery, JsonListModel
    taos_client.h (C ABI)    native/taos-client-ffi  one callback, integer ids, JSON in and out
      taos-client (Rust)     native/taos-client      REST, SSE, cookie session, CSRF, error mapping
```

Interfaces are QML and C++. Rust is used only for the UI-less client. No cxx-qt.

Build and test (Qt 6.8 is the floor; deprecated Qt API is an error from 6.8):

```text
native/qt/check.sh                 # cmake + ninja -j2 + ctest, offscreen
cmake -S native/qt -B build -DTAOS_CARGO_OFFLINE=ON   # use the cached crates
```

The tests run against `native/testing/fake_controller.py`, a stdlib Python
server that enforces the session cookie (or bearer token), the CSRF header on
mutations and the constant `User-Agent`, and can delay, fail, expire sessions,
drop the event stream and burst events on command.

## Using it

```cpp
Taos::Session session;                                   // controller at 127.0.0.1:6969
connect(&session, &Taos::Session::signInRequired, this, &MyApp::showSignIn);
session.start();                                         // later: session.signIn(user, password)

// one request
Taos::Notifications(session.client()).list()->then(this, [](const Taos::Result &r) {
    if (!r.ok()) { status(r.error.userMessage()); return; }
    use(r.value.toArray());
});

// a list that stays current (the usual app pattern)
auto *q = new Taos::LiveQuery(session.client(), u"notifications.list"_s, {},
                              {u"notification.added"_s}, {u"id"_s, u"title"_s, u"read"_s}, this);
q->start();        // QML: ListView { model: q.model }; q.live, q.loading, q.errorText
```

Rules the layer keeps for you:

* A 401 anywhere (request or stream) is ONE `Session::signInRequired` (and one
  `Client::unauthenticated`), however many calls fail together. Streams that
  stopped for lack of a session restart by themselves after sign-in.
* The controller being unreachable is `Client::Reconnecting` plus
  `Error::Network`; streams retry with backoff by themselves.
* `Error::userMessage()` is the sentence to show (the controller's own words
  when it sent them). Never build text from the kind.
* Deleting a `Reply`, `EventStream`, `LiveQuery` or the `Client` cancels the
  work in the transport (the server sees the connection close) and nothing is
  delivered afterwards.
* All signals arrive on the thread that owns the `Client`.

## Replacing the credential source

`Session` asks a `TokenSource` for credentials (`session.h`). Shipped: password
(the sign-in form), the host-local bearer token, a saved session from the
keyring. For the per-person session of the accounts design
(`files-and-accounts.md` section 4; apps hold a scoped token, never the user's
session, `app-model.md` section 3) write a `TokenSource` whose `acquire()`
asks the account service and calls `Session::useBearerToken()`. No app changes.

## App conventions

* App id: `my.taos.<name>` (lower case). The same string is the Wayland
  `app_id` (`QGuiApplication::setDesktopFileName`, done by `Taos::App::configure`),
  the `.desktop` file base name, `StartupWMClass`, the icon name and the
  single-instance key, so the window switcher can show the name and icon.
* `app/app.desktop.in` is the template; `cmake/TaosNativeApp.cmake`
  (`taos_add_native_app`) builds the executable and writes and installs
  `<id>.desktop`.
* One instance per app and user: `Taos::SingleInstance` (QLocalServer; the
  second launch forwards its arguments and exits).
* QML style: `style/Taos/Style` (`TaosStyle` tokens named like the
  `taos-default` theme, and `TaosWindow` with a connection banner). It becomes a
  binding to `native/shell/common`'s `Theme` once that library is merged.

## Not covered here

Typed C++ structs for the rows (apps get JSON; `JsonListModel` exposes the
keys as roles), WebSocket chat, the file domain ops (`taos-client` has them;
add ops to `taos-client-ffi/src/ops.rs` and the header).
