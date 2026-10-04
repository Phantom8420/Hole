# Hole for Android

The same Hole as the desktop app, on a phone: the dashboard, the services you browse for
postings and competitions, an inbox for what you capture, and a button that asks the server to
run the pipeline now.

It is a **remote for the server, not the pipeline on the phone.** The 12:00 GMT run, the
filtering, the tailoring and the applying all happen on the server (Turso behind the Oracle VM),
so they happen whether or not this app is open, and Android killing a background app costs
nothing. The phone is for looking, for deciding, and for feeding the server things you found.

## What is in it

- **The rail** along the bottom: Hole, LinkedIn, Indeed, Discord, Proofr, Unstop, Devfolio,
  Devpost, MLH, and Settings. The same marks, the same order and the same access dots as the
  desktop rail (grey is view-only, green is capture on request), and the same palette.
- **Hole** is the dashboard itself, in a WebView: to apply, applied, freelance and contract, upcoming
  competitions, the Pipeline panel. You sign in with the same web password, typed into the page.
  Links out to a posting open in your browser.
- **The pipeline line and Update listings** under the toolbar say how the pipeline stands (the
  same wording as the desktop) and ask the server to start a run now. Asking twice is harmless.
- **Capture** reads the page you are looking at in a service tab, when you press it, with the
  desktop app's own extractors, into the **inbox**. Nothing is read in the background.
- **Share to Hole**: in the LinkedIn, Indeed or Discord app (or Chrome), Share, then Hole. The
  text and link go to the inbox; nothing is fetched.
- **The inbox** is where you correct, tick and send. Only ticked items go, to `/api/ingest`, and
  a job needs a title and a company, as on the desktop.

Like the desktop app it does nothing on LinkedIn, Indeed or Discord beyond what you do yourself
and read on request. There is no automation of those sites, and no disguise of what the app is.

## Credentials

Two, the same two as everywhere else:

| | where it goes | how it is kept |
|---|---|---|
| web password | typed into the Hole page's login form | never stored by the app; the page's session cookie lasts what the server says |
| `JOBSEARCH_API_TOKEN` | Settings (the gear), pasted once | AES-GCM with a key in the Android Keystore; the file on disk is useless without this app on this phone |

Without the token the Hole tab still works completely (its own Pipeline panel has the button); the
pipeline line, Update listings and sending the inbox need it.

The server address is `https://hole-roan.vercel.app` unless you change it in Settings. It must be
https: the app sends a bearer token and a session cookie there, and Android refuses cleartext
traffic anyway. Backups are off.

## Building

You need a JDK 17 or later and the Android SDK (platform 36, build-tools 36). Android Studio brings
both: open this folder in it and press Run, or from a terminal:

```
cd android
gradlew assembleDebug          # app/build/outputs/apk/debug/app-debug.apk
gradlew testDebugUnitTest      # the JVM tests
```

If `java` is not a JDK 17+ on your path, point `JAVA_HOME` at one (Android Studio's is its `jbr`
folder). The project lives in OneDrive on this machine; Gradle's caches and `build/` are big and
churn, so it is kinder to build from a copy outside it (robocopy the folder to `%TEMP%`).

The APK is debug-signed, which is all sideloading needs. Copy it to the phone, allow "install
unknown apps" for whatever opens it, and install. A later build signed with a different key will not
install over it: uninstall first, or keep one `~/.android/debug.keystore` for every build (Gradle
makes it, and reuses it, on its own). There is no release keystore and no Play listing.

## Keeping it the same as the desktop

The look and the lists are not copied by hand. `tools/sync.js` writes them from the desktop app's own
files, and `desktop/test/android-sync.test.js` (run by `npm test` in `desktop/`) fails when they are
stale:

| desktop | android |
|---|---|
| `shell/icons.js` | `res/drawable/ic_*.xml`, and the launcher icon |
| `src/services.js` | `assets/services.json` |
| `src/extractors.js` | `assets/extractors.js` |
| `shell/shell.css` | `res/values/hole_colors.xml` |

After changing any of them: `node android/tools/sync.js`.

## What is and is not tested

JVM unit tests cover the parts with logic: the status wording (the desktop's own cases), the API
client against a real HTTP server on loopback standing in for Hole, the inbox, the share-text parser,
the capture parsing, addresses, and the service list. The layouts and manifest are checked by the
build and by lint.

**Nothing here has been run on a phone or an emulator.** The WebView behaviour (sign-in, tabs, capture
on a live page, the Keystore) and the share sheet are the parts to try first; a crash prints to
`adb logcat` or Android Studio's Logcat, and the debug build can be inspected from
`chrome://inspect` on a computer.

## Limits

- Google's sign-in refuses to run inside a WebView, so a service that only offers "Sign in with Google"
  needs its own app or an email login.
- Files cannot be uploaded from, or downloaded into, the tabs yet.
- `targetSdk` is 34. From 35 Android draws the system bars over the app, which every screen would
  have to be laid out for; raise it with that work.
- The Discord tab is the web app, which Discord steers phones away from. Its own app plus Share to
  Hole is the better route.
