# NammaUruCab

## Static platform demo

Open `index.html` in a browser to try the customer booking estimate, B2B margin calculator, and driver onboarding flow. The page is a front-end prototype: prices are estimates, OTP verification is simulated, and submissions are handed off to WhatsApp rather than stored or confirmed by a backend. Do not enter bank account numbers or other sensitive information.

## GitHub Pages

The `Deploy static site to GitHub Pages` workflow publishes the repository root whenever changes reach `main`. Merge the website and workflow into `main`, then check the workflow run under **Actions** for the published URL. The workflow also supports manual runs from the Actions tab.

## Android app

The `android` directory contains a native Android app that opens the public website, sends phone and WhatsApp links to Android, and provides an offline retry screen. Open that directory in Android Studio or build a debug APK from a terminal:

```sh
cd android
./gradlew assembleDebug
```

On Windows, run `gradlew.bat assembleDebug`. The debug APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`. The Android Actions workflow also builds this APK for pull requests and uploads it as a downloadable artifact; a release build for publishing on Google Play requires a signing key.

### Signed Google Play release

For a Play Store release, create and securely back up an upload keystore. Enroll in Play App Signing and use this key as the upload key. Never commit the keystore or its passwords.

For example, create an upload key locally with:

```sh
keytool -genkeypair -v -keystore namma-uru-cab-upload.jks -keyalg RSA -keysize 2048 -validity 10000 -alias namma-uru-cab-upload
```

Add these repository Actions secrets, or define them in the `google-play-release` environment:

- `ANDROID_RELEASE_KEYSTORE_BASE64`: the upload keystore file encoded as a single-line Base64 string.
- `ANDROID_RELEASE_KEYSTORE_PASSWORD`: the keystore password.
- `ANDROID_RELEASE_KEY_ALIAS`: the signing key alias.
- `ANDROID_RELEASE_KEY_PASSWORD`: the signing key password.

The Android workflow builds a signed `.aab` on a `v*` tag push. It can also be run from **Actions > Build Android app > Run workflow** by setting **Build a signed Android App Bundle for Google Play** to true. Download the `namma-uru-cab-google-play-release` artifact and upload it to Play Console. Release build numbers are assigned by the workflow; a tag such as `v1.2.3` sets the app version name to `1.2.3`. No signing key is stored in the repository.