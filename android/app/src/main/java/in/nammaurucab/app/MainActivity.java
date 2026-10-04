package in.nammaurucab.app;

import android.app.Activity;
import android.content.Context;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.view.WindowInsets;
import android.webkit.SslErrorHandler;
import android.webkit.WebChromeClient;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import android.net.http.SslError;
import org.json.JSONObject;

public final class MainActivity extends Activity {
    private static final int LOCATION_PICKER_REQUEST = 43;
    private static final String HOME_URL = "https://jnravr-del.github.io/NammaUruCab/";
    private static final String SITE_HOST = "jnravr-del.github.io";
    private static final int BRAND_NAVY = Color.rgb(6, 22, 43);

    private WebView webView;
    private View errorView;
    private ProgressBar progressBar;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        configureSystemBars();
        createContent();

        if (savedInstanceState == null) {
            webView.loadUrl(HOME_URL);
        } else {
            webView.restoreState(savedInstanceState);
        }
    }

    private void configureSystemBars() {
        Window window = getWindow();
        window.setStatusBarColor(BRAND_NAVY);
        window.setNavigationBarColor(BRAND_NAVY);

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.setDecorFitsSystemWindows(false);
            window.getDecorView().setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars());
                view.setPadding(bars.left, bars.top, bars.right, bars.bottom);
                return insets;
            });
        }
    }

    private void createContent() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);

        webView = new WebView(this);
        webView.setBackgroundColor(Color.WHITE);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(false);
        settings.setSupportMultipleWindows(false);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            settings.setSafeBrowsingEnabled(true);
        }
        webView.addJavascriptInterface(new PrintBridge(), "NammaUruCabAndroid");

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int progress) {
                progressBar.setProgress(progress);
                progressBar.setVisibility(progress >= 100 ? View.GONE : View.VISIBLE);
            }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return routeUrl(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, String url) {
                return routeUrl(Uri.parse(url));
            }

            @Override
            public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
                errorView.setVisibility(View.GONE);
                webView.setVisibility(View.VISIBLE);
                progressBar.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progressBar.setVisibility(View.GONE);
                if (isTrustedSite(url)) {
                    view.evaluateJavascript(
                            "(function(){"
                                    + "var fields=[['custPickup','pickup'],['custDrop','drop']];"
                                    + "fields.forEach(function(item){"
                                    + "var input=document.getElementById(item[0]);"
                                    + "if(!input||document.getElementById(item[0]+'MapButton'))return;"
                                    + "var button=document.createElement('button');"
                                    + "button.id=item[0]+'MapButton';"
                                    + "button.type='button';"
                                    + "button.textContent='Choose on Google Maps';"
                                    + "button.setAttribute('aria-label','Choose '+item[1]+' location on Google Maps');"
                                    + "button.style.cssText='display:block;margin-top:6px;padding:5px 8px;border:0;"
                                    + "border-radius:8px;background:#eaf1f8;color:#104c8c;font-size:11px;"
                                    + "font-weight:700;cursor:pointer';"
                                    + "button.addEventListener('click',function(){"
                                    + "window.NammaUruCabAndroid.openLocationPicker(item[1],input.value);"
                                    + "});input.parentElement.appendChild(button);"
                                    + "input.addEventListener('input',function(){"
                                    + "if(input.dataset.mapSelectionUpdate){delete input.dataset.mapSelectionUpdate;return;}"
                                    + "delete input.dataset.latitude;delete input.dataset.longitude;"
                                    + "});"
                                    + "});})()",
                            null
                    );
                }
                view.evaluateJavascript(
                        "(function(){window.open=function(url){window.location.href=url;return null;};"
                                + "window.print=function(){window.NammaUruCabAndroid.printVoucher();};})()",
                        null
                );
            }

            @Override
            public void onReceivedError(
                    WebView view,
                    WebResourceRequest request,
                    android.webkit.WebResourceError error
            ) {
                if (request.isForMainFrame()) {
                    showLoadError();
                }
            }

            @Override
            public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
                handler.cancel();
            }
        });

        root.addView(webView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        progressBar = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progressBar.setIndeterminate(false);
        progressBar.setMax(100);
        progressBar.setProgressTintList(android.content.res.ColorStateList.valueOf(BRAND_NAVY));
        FrameLayout.LayoutParams progressLayout = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                dp(3),
                Gravity.TOP
        );
        root.addView(progressBar, progressLayout);

        errorView = createErrorView();
        errorView.setVisibility(View.GONE);
        root.addView(errorView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        setContentView(root);
    }

    private View createErrorView() {
        LinearLayout container = new LinearLayout(this);
        container.setGravity(Gravity.CENTER);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(dp(32), dp(32), dp(32), dp(32));
        container.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("Can’t connect right now");
        title.setTextColor(BRAND_NAVY);
        title.setTextSize(22);
        title.setGravity(Gravity.CENTER);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        container.addView(title);

        TextView message = new TextView(this);
        message.setText("Check your internet connection and try again.");
        message.setTextColor(Color.DKGRAY);
        message.setTextSize(15);
        message.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams messageLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        messageLayout.topMargin = dp(12);
        container.addView(message, messageLayout);

        Button retry = new Button(this);
        retry.setText("Try again");
        retry.setOnClickListener(view -> webView.loadUrl(HOME_URL));
        LinearLayout.LayoutParams buttonLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        buttonLayout.topMargin = dp(20);
        container.addView(retry, buttonLayout);
        return container;
    }

    private boolean isTrustedSite(String url) {
        Uri uri = Uri.parse(url);
        return "https".equalsIgnoreCase(uri.getScheme())
                && SITE_HOST.equalsIgnoreCase(uri.getHost())
                && (uri.getPort() == -1 || uri.getPort() == 443);
    }

    private void openLocationPicker(String field, String currentValue) {
        if (!"pickup".equals(field) && !"drop".equals(field)) {
            return;
        }
        Intent intent = new Intent(this, MapPickerActivity.class);
        intent.putExtra("field", field);
        intent.putExtra("current_value", currentValue);
        startActivityForResult(intent, LOCATION_PICKER_REQUEST);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != LOCATION_PICKER_REQUEST || resultCode != RESULT_OK || data == null) {
            return;
        }
        String field = data.getStringExtra("field");
        String inputId;
        if ("pickup".equals(field)) {
            inputId = "custPickup";
        } else if ("drop".equals(field)) {
            inputId = "custDrop";
        } else {
            return;
        }
        String address = data.getStringExtra("address");
        if (address == null || address.trim().isEmpty()) {
            return;
        }
        String script = "(function(){var input=document.getElementById("
                + JSONObject.quote(inputId)
                + ");if(input){input.value="
                + JSONObject.quote(address)
                + ";input.dataset.latitude="
                + Double.toString(data.getDoubleExtra("latitude", 0))
                + ";input.dataset.longitude="
                + Double.toString(data.getDoubleExtra("longitude", 0))
                + ";input.dataset.mapSelectionUpdate='true'"
                + ";input.dispatchEvent(new Event('input',{bubbles:true}));}})()";
        webView.evaluateJavascript(script, null);
    }

    private boolean routeUrl(Uri uri) {
        String scheme = uri.getScheme();
        if (scheme == null) {
            return true;
        }

        if ("https".equalsIgnoreCase(scheme)
                && SITE_HOST.equalsIgnoreCase(uri.getHost())
                && (uri.getPort() == -1 || uri.getPort() == 443)) {
            return false;
        }

        if ("https".equalsIgnoreCase(scheme)
                || "tel".equalsIgnoreCase(scheme)
                || "mailto".equalsIgnoreCase(scheme)
                || "sms".equalsIgnoreCase(scheme)) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, uri));
            } catch (ActivityNotFoundException exception) {
                Toast.makeText(this, "No app can open this link.", Toast.LENGTH_SHORT).show();
            }
        }
        return true;
    }

    private void showLoadError() {
        webView.setVisibility(View.GONE);
        progressBar.setVisibility(View.GONE);
        errorView.setVisibility(View.VISIBLE);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void printVoucher() {
        android.print.PrintManager printManager =
                (android.print.PrintManager) getSystemService(Context.PRINT_SERVICE);
        if (printManager == null) {
            Toast.makeText(this, "Printing is unavailable on this device.", Toast.LENGTH_SHORT).show();
            return;
        }
        printManager.print(
                "Namma Uru Cab voucher",
                webView.createPrintDocumentAdapter("Namma Uru Cab voucher"),
                new android.print.PrintAttributes.Builder().build()
        );
    }

    private final class PrintBridge {
        @JavascriptInterface
        public void openLocationPicker(String field, String currentValue) {
            if (!isTrustedSite(webView.getUrl())) {
                return;
            }
            runOnUiThread(() -> MainActivity.this.openLocationPicker(field, currentValue));
        }

        @JavascriptInterface
        public void printVoucher() {
            runOnUiThread(MainActivity.this::printVoucher);
        }
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    @Override
    protected void onDestroy() {
        if (webView != null) {
            webView.destroy();
        }
        super.onDestroy();
    }
}
