package in.nammaurucab.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ApplicationInfo;
import android.graphics.Color;
import android.location.Address;
import android.location.Geocoder;
import android.location.Location;
import android.location.LocationManager;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import com.google.android.gms.maps.CameraUpdateFactory;
import com.google.android.gms.maps.GoogleMap;
import com.google.android.gms.maps.MapView;
import com.google.android.gms.maps.OnMapReadyCallback;
import com.google.android.gms.maps.model.BitmapDescriptorFactory;
import com.google.android.gms.maps.model.LatLng;
import com.google.android.gms.maps.model.Marker;
import com.google.android.gms.maps.model.MarkerOptions;

import java.io.IOException;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MapPickerActivity extends Activity implements OnMapReadyCallback {
    private static final int LOCATION_PERMISSION_REQUEST = 42;
    private static final int NAVY = Color.rgb(6, 22, 43);
    private static final LatLng BENGALURU = new LatLng(12.9716, 77.5946);

    private final ExecutorService geocoderExecutor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private MapView mapView;
    private GoogleMap googleMap;
    private Marker pin;
    private EditText searchInput;
    private TextView addressText;
    private String field;
    private String address = "Bengaluru, Karnataka";
    private int geocodeRequest;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        field = getIntent().getStringExtra("field");
        if (!"pickup".equals(field) && !"drop".equals(field)) {
            setResult(RESULT_CANCELED);
            finish();
            return;
        }

        getWindow().setStatusBarColor(NAVY);
        getWindow().setNavigationBarColor(NAVY);
        if (!hasMapsApiKey()) {
            setContentView(createMapsSetupView());
            return;
        }
        createContent();
        mapView.onCreate(savedInstanceState);
        mapView.getMapAsync(this);

        String currentValue = getIntent().getStringExtra("current_value");
        if (currentValue != null && !currentValue.trim().isEmpty()) {
            searchInput.setText(currentValue);
        }
    }

    private boolean hasMapsApiKey() {
        try {
            ApplicationInfo applicationInfo = getPackageManager().getApplicationInfo(
                    getPackageName(), PackageManager.GET_META_DATA);
            String apiKey = applicationInfo.metaData == null
                    ? null
                    : applicationInfo.metaData.getString("com.google.android.geo.API_KEY");
            return apiKey != null && !apiKey.trim().isEmpty()
                    && !apiKey.contains("YOUR_GOOGLE_MAPS_API_KEY");
        } catch (PackageManager.NameNotFoundException exception) {
            return false;
        }
    }

    private View createMapsSetupView() {
        LinearLayout container = new LinearLayout(this);
        container.setGravity(Gravity.CENTER);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(dp(28), dp(28), dp(28), dp(28));
        container.setBackgroundColor(Color.WHITE);

        TextView title = new TextView(this);
        title.setText("Google Maps needs setup");
        title.setTextColor(NAVY);
        title.setTextSize(22);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        title.setGravity(Gravity.CENTER);
        container.addView(title);

        TextView message = new TextView(this);
        message.setText("Add a restricted Google Maps API key to the Android build, then rebuild the app. You can still enter the address manually.");
        message.setTextColor(Color.DKGRAY);
        message.setTextSize(15);
        message.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams messageLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        messageLayout.topMargin = dp(12);
        container.addView(message, messageLayout);

        Button backButton = new Button(this);
        backButton.setText("Back to booking");
        backButton.setOnClickListener(view -> finish());
        LinearLayout.LayoutParams buttonLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        buttonLayout.topMargin = dp(20);
        container.addView(backButton, buttonLayout);
        return container;
    }

    private void createContent() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);

        mapView = new MapView(this);
        root.addView(mapView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.MATCH_PARENT
        ));

        LinearLayout topPanel = new LinearLayout(this);
        topPanel.setOrientation(LinearLayout.VERTICAL);
        topPanel.setPadding(dp(16), dp(12), dp(16), dp(12));
        topPanel.setBackgroundColor(Color.WHITE);
        topPanel.setElevation(dp(4));

        TextView title = new TextView(this);
        title.setText("Choose " + ("pickup".equals(field) ? "pickup" : "drop-off") + " location");
        title.setTextColor(NAVY);
        title.setTextSize(18);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        topPanel.addView(title);

        TextView hint = new TextView(this);
        hint.setText("Search a place, or move the map until the pin is on your location.");
        hint.setTextColor(Color.DKGRAY);
        hint.setTextSize(13);
        LinearLayout.LayoutParams hintLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        hintLayout.topMargin = dp(4);
        topPanel.addView(hint, hintLayout);

        LinearLayout searchRow = new LinearLayout(this);
        searchRow.setGravity(Gravity.CENTER_VERTICAL);
        searchInput = new EditText(this);
        searchInput.setSingleLine(true);
        searchInput.setHint("Search city, area or address");
        searchInput.setTextSize(14);
        searchInput.setPadding(dp(12), 0, dp(8), 0);
        searchRow.addView(searchInput, new LinearLayout.LayoutParams(
                0,
                dp(48),
                1
        ));

        Button searchButton = new Button(this);
        searchButton.setText("Search");
        searchButton.setOnClickListener(view -> searchPlace());
        searchRow.addView(searchButton);
        LinearLayout.LayoutParams searchLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        searchLayout.topMargin = dp(6);
        topPanel.addView(searchRow, searchLayout);

        FrameLayout.LayoutParams topPanelLayout = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
        );
        topPanelLayout.setMargins(dp(12), dp(12), dp(12), 0);
        root.addView(topPanel, topPanelLayout);

        View centerPin = new View(this);
        centerPin.setBackgroundColor(Color.TRANSPARENT);
        FrameLayout.LayoutParams pinLayout = new FrameLayout.LayoutParams(dp(36), dp(48), Gravity.CENTER);
        root.addView(centerPin, pinLayout);
        TextView pinIcon = new TextView(this);
        pinIcon.setText("●");
        pinIcon.setTextColor(Color.rgb(208, 150, 40));
        pinIcon.setTextSize(32);
        pinIcon.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams iconLayout = new FrameLayout.LayoutParams(dp(48), dp(48), Gravity.CENTER);
        iconLayout.bottomMargin = dp(12);
        root.addView(pinIcon, iconLayout);

        Button locationButton = new Button(this);
        locationButton.setText("My location");
        locationButton.setOnClickListener(view -> moveToCurrentLocation());
        FrameLayout.LayoutParams locationLayout = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.WRAP_CONTENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.END | Gravity.CENTER_VERTICAL
        );
        locationLayout.setMargins(0, dp(80), dp(12), dp(32));
        root.addView(locationButton, locationLayout);

        LinearLayout bottomPanel = new LinearLayout(this);
        bottomPanel.setOrientation(LinearLayout.VERTICAL);
        bottomPanel.setPadding(dp(16), dp(12), dp(16), dp(12));
        bottomPanel.setBackgroundColor(Color.WHITE);
        bottomPanel.setElevation(dp(8));

        addressText = new TextView(this);
        addressText.setText(address);
        addressText.setTextColor(NAVY);
        addressText.setTextSize(14);
        addressText.setMaxLines(2);
        bottomPanel.addView(addressText);

        Button confirmButton = new Button(this);
        confirmButton.setText("Use this " + ("pickup".equals(field) ? "pickup" : "drop-off"));
        confirmButton.setOnClickListener(view -> confirmLocation());
        LinearLayout.LayoutParams confirmLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        confirmLayout.topMargin = dp(6);
        bottomPanel.addView(confirmButton, confirmLayout);

        FrameLayout.LayoutParams bottomPanelLayout = new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT,
                FrameLayout.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM
        );
        bottomPanelLayout.setMargins(dp(12), 0, dp(12), dp(12));
        root.addView(bottomPanel, bottomPanelLayout);
        setContentView(root);
    }

    @Override
    public void onMapReady(GoogleMap map) {
        googleMap = map;
        googleMap.getUiSettings().setMapToolbarEnabled(false);
        googleMap.setOnCameraIdleListener(() -> {
            LatLng target = googleMap.getCameraPosition().target;
            if (pin == null) {
                pin = googleMap.addMarker(new MarkerOptions()
                        .position(target)
                        .draggable(false)
                        .icon(BitmapDescriptorFactory.defaultMarker(BitmapDescriptorFactory.HUE_ORANGE)));
            } else {
                pin.setPosition(target);
            }
            reverseGeocode(target);
        });

        googleMap.moveCamera(CameraUpdateFactory.newLatLngZoom(BENGALURU, 12));
        if (hasLocationPermission()) {
            enableLocationLayer();
        }
    }

    private void searchPlace() {
        String query = searchInput.getText().toString().trim();
        if (query.isEmpty()) {
            searchInput.setError("Enter a place or address");
            return;
        }
        geocoderExecutor.execute(() -> {
            try {
                Geocoder geocoder = new Geocoder(this, Locale.getDefault());
                List<Address> results = geocoder.getFromLocationName(query, 1);
                if (results == null || results.isEmpty() || !results.get(0).hasLatitude()) {
                    mainHandler.post(() ->
                            Toast.makeText(this, "No matching location found. Try another search.", Toast.LENGTH_SHORT).show());
                    return;
                }
                Address result = results.get(0);
                LatLng position = new LatLng(result.getLatitude(), result.getLongitude());
                mainHandler.post(() -> {
                    if (googleMap != null) {
                        googleMap.animateCamera(CameraUpdateFactory.newLatLngZoom(position, 16));
                    }
                });
            } catch (IOException | IllegalArgumentException exception) {
                mainHandler.post(() ->
                        Toast.makeText(this, "Location search is unavailable. Move the map to choose a point.", Toast.LENGTH_LONG).show());
            }
        });
    }

    private void reverseGeocode(LatLng position) {
        int request = ++geocodeRequest;
        address = String.format(Locale.getDefault(), "%.5f, %.5f", position.latitude, position.longitude);
        addressText.setText(address);
        geocoderExecutor.execute(() -> {
            String label = null;
            try {
                Geocoder geocoder = new Geocoder(this, Locale.getDefault());
                List<Address> results = geocoder.getFromLocation(position.latitude, position.longitude, 1);
                if (results != null && !results.isEmpty()) {
                    label = results.get(0).getAddressLine(0);
                }
            } catch (IOException | IllegalArgumentException ignored) {
                // Coordinates remain a valid location choice when reverse geocoding is unavailable.
            }
            if (label != null && !label.trim().isEmpty()) {
                String resolvedLabel = label;
                mainHandler.post(() -> {
                    if (request == geocodeRequest) {
                        address = resolvedLabel;
                        addressText.setText(resolvedLabel);
                    }
                });
            }
        });
    }

    private void moveToCurrentLocation() {
        if (!hasLocationPermission()) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                requestPermissions(
                        new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION},
                        LOCATION_PERMISSION_REQUEST
                );
            }
            return;
        }
        enableLocationLayer();
        LocationManager locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        Location latest = null;
        for (String provider : new String[]{LocationManager.NETWORK_PROVIDER, LocationManager.GPS_PROVIDER}) {
            try {
                Location candidate = locationManager.getLastKnownLocation(provider);
                if (candidate != null && (latest == null || candidate.getTime() > latest.getTime())) {
                    latest = candidate;
                }
            } catch (SecurityException ignored) {
                // The permission can be revoked between the check and provider lookup.
            }
        }
        if (latest != null && googleMap != null) {
            googleMap.animateCamera(CameraUpdateFactory.newLatLngZoom(
                    new LatLng(latest.getLatitude(), latest.getLongitude()), 16));
        } else {
            Toast.makeText(this, "Current location is not available yet.", Toast.LENGTH_SHORT).show();
        }
    }

    private boolean hasLocationPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void enableLocationLayer() {
        if (googleMap == null || !hasLocationPermission()) {
            return;
        }
        try {
            googleMap.setMyLocationEnabled(true);
        } catch (SecurityException ignored) {
            // Location is optional; map selection still works without it.
        }
    }

    private void confirmLocation() {
        if (googleMap == null) {
            return;
        }
        LatLng target = googleMap.getCameraPosition().target;
        reverseGeocode(target);
        Intent result = new Intent();
        result.putExtra("field", field);
        result.putExtra("address", address);
        result.putExtra("latitude", target.latitude);
        result.putExtra("longitude", target.longitude);
        setResult(RESULT_OK, result);
        finish();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == LOCATION_PERMISSION_REQUEST && hasLocationPermission()) {
            moveToCurrentLocation();
        }
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    @Override
    protected void onStart() {
        super.onStart();
        if (mapView != null) mapView.onStart();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (mapView != null) mapView.onResume();
    }

    @Override
    protected void onPause() {
        if (mapView != null) mapView.onPause();
        super.onPause();
    }

    @Override
    protected void onStop() {
        if (mapView != null) mapView.onStop();
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        geocoderExecutor.shutdownNow();
        if (mapView != null) mapView.onDestroy();
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (mapView != null) mapView.onSaveInstanceState(outState);
    }

    @Override
    public void onLowMemory() {
        super.onLowMemory();
        if (mapView != null) mapView.onLowMemory();
    }
}
