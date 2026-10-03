# NammaUruCab

## Static platform demo

Open `index.html` in a browser to try the customer booking estimate, B2B margin calculator, and driver onboarding flow. The page is a front-end prototype: prices are estimates, OTP verification is simulated, and submissions are handed off to WhatsApp rather than stored or confirmed by a backend. Do not enter bank account numbers or other sensitive information.

## Booking API

The repository also contains a Python WSGI JSON API with SQLite persistence for customer authentication, cab availability and booking, vendor/driver onboarding, admin dispatch, in-app notifications, and contact requests. It runs separately from GitHub Pages; setup, endpoint contracts, and deployment assumptions are documented in [`api/README.md`](api/README.md). Start the local development server with `python -m api.app`.

The customer website is integrated with the API for live cab search, booking requests and tracking, account sign-in, vendor/driver signup, account management, contact requests, and the admin dispatch dashboard. The current GitHub Pages URL will use the configured Render API URL once that service has been provisioned. See the online deployment steps in [`api/README.md`](api/README.md); the API is not hosted by GitHub Pages itself.

To provision the backend, connect the repository in [Render Blueprint deploy](https://render.com/deploy?repo=https://github.com/jnravr-del/NammaUruCab), set the private administrator credentials, and deploy the configured service. Render requires an account and a paid service with persistent storage; real bookings are not available until this backend is live.

## GitHub Pages

The `Deploy static site to GitHub Pages` workflow publishes the repository root whenever changes reach `main`. Merge the website and workflow into `main`, then check the workflow run under **Actions** for the published URL. The workflow also supports manual runs from the Actions tab.

## Android app

The `android` directory contains a native Android app that opens the public website, sends phone and WhatsApp links to Android, and provides an offline retry screen. Open that directory in Android Studio or build a debug APK from a terminal:

```sh
cd android
./gradlew assembleDebug
```

On Windows, run `gradlew.bat assembleDebug`. The debug APK is written to `android/app/build/outputs/apk/debug/app-debug.apk`. The Android Actions workflow also builds this APK for pull requests and uploads it as a downloadable artifact; a release build for publishing on Google Play requires a signing key.