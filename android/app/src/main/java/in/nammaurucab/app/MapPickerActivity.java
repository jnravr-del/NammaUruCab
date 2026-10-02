package in.nammaurucab.app;

import android.Manifest;
import android.app.Activity;
import android.content.Intent;
import android.content.pm.PackageManager;
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

import org.osmdroid.config.Configuration;
import org.osmdroid.events.MapListener;
import org.osmdroid.events.ScrollEvent;
import org.osmdroid.events.ZoomEvent;
import org.osmdroid.tileprovider.tilesource.TileSourceFactory;
import org.osmdroid.util.GeoPoint;
import org.osmdroid.views.MapView;

import java.io.IOException;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MapPickerActivity extends Activity {
    private static final int LOCATION_PERMISSION_REQUEST = 42;
    private static final int NAVY = Color.rgb(6, 22, 43);
    private static final GeoPoint BENGALURU = new GeoPoint(12.9716, 77.5946);
    private static final long GEOCODE_DEBOUNCE_MS = 450;

    private final ExecutorService geocoderExecutor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Runnable reverseGeocodeAction = this::reverseGeocodeCenter;

    private MapView mapView;
    private EditText searchInput;
    private TextView addressText;
    private Button confirmButton;
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

        Configuration.getInstance().load(
                getApplicationContext(),
                getSharedPreferences("osmdroid", MODE_PRIVATE)
        );
        Configuration.getInstance().setUserAgentValue(getPackageName());

        getWindow().setStatusBarColor(NAVY);
        getWindow().setNavigationBarColor(NAVY);
        createContent();
        configureMap(savedInstanceState);

        String currentValue = getIntent().getStringExtra("current_value");
        if (currentValue != null && !currentValue.trim().isEmpty()) {
            searchInput.setText(currentValue);
        }
    }

    private void createContent() {
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.WHITE);

        mapView = new MapView(this);
        mapView.setTileSource(TileSourceFactory.MAPNIK);
        mapView.setMultiTouchControls(true);
        mapView.setBuiltInZoomControls(false);
        mapView.setTilesScaledToDpi(true);
        mapView.setMinZoomLevel(4.0);
        mapView.setMaxZoomLevel(19.0);
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
        searchRow.addView(searchInput, new LinearLayout.LayoutParams(0, dp(48), 1));

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

        TextView pin = new TextView(this);
        pin.setText("●");
        pin.setTextColor(Color.rgb(208, 150, 40));
        pin.setTextSize(32);
        pin.setGravity(Gravity.CENTER);
        FrameLayout.LayoutParams pinLayout = new FrameLayout.LayoutParams(dp(48), dp(48), Gravity.CENTER);
        pinLayout.bottomMargin = dp(12);
        root.addView(pin, pinLayout);

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

        TextView attribution = new TextView(this);
        attribution.setText("Map data © OpenStreetMap contributors");
        attribution.setTextColor(Color.DKGRAY);
        attribution.setTextSize(11);
        LinearLayout.LayoutParams attributionLayout = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT
        );
        attributionLayout.topMargin = dp(4);
        bottomPanel.addView(attribution, attributionLayout);

        confirmButton = new Button(this);
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

    private void configureMap(Bundle savedInstanceState) {
        GeoPoint initialPosition = BENGALURU;
        String currentValue = getIntent().getStringExtra("current_value");
        if (currentValue != null && !currentValue.trim().isEmpty()) {
            String normalized = currentValue.toLowerCase(Locale.ROOT);
            if (normalized.contains("mysore") || normalized.contains("mysuru")) {
                initialPosition = new GeoPoint(12.2958, 76.6394);
            } else if (normalized.contains("airport") || normalized.contains("blr")) {
                initialPosition = new GeoPoint(13.1986, 77.7066);
            }
        }
        mapView.onCreate(savedInstanceState);
        mapView.getController().setZoom(13.0);
        mapView.getController().setCenter(initialPosition);
        mapView.setMapListener(new MapListener() {
            @Override
            public boolean onScroll(ScrollEvent event) {
                scheduleReverseGeocode();
                return true;
            }

            @Override
            public boolean onZoom(ZoomEvent event) {
                scheduleReverseGeocode();
                return true;
            }
        });
        scheduleReverseGeocode();
    }

    private void scheduleReverseGeocode() {
        mainHandler.removeCallbacks(reverseGeocodeAction);
        mainHandler.postDelayed(reverseGeocodeAction, GEOCODE_DEBOUNCE_MS);
    }

    private void searchPlace() {
        String query = searchInput.getText().toString().trim();
        if (query.isEmpty()) {
            searchInput.setError("Enter a place or address");
            return;
        }
        searchInput.clearFocus();
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
                GeoPoint position = new GeoPoint(result.getLatitude(), result.getLongitude());
                mainHandler.post(() -> {
                    mapView.getController().animateTo(position);
                    mapView.getController().setZoom(17.0);
                    scheduleReverseGeocode();
                });
            } catch (IOException | IllegalArgumentException exception) {
                mainHandler.post(() ->
                        Toast.makeText(this, "Location search is unavailable. Move the map to choose a point.", Toast.LENGTH_LONG).show());
            }
        });
    }

    private void reverseGeocodeCenter() {
        GeoPoint center = mapView.getMapCenter() instanceof GeoPoint
                ? (GeoPoint) mapView.getMapCenter()
                : BENGALURU;
        int request = ++geocodeRequest;
        address = String.format(Locale.getDefault(), "%.5f, %.5f", center.getLatitude(), center.getLongitude());
        addressText.setText(address);
        geocoderExecutor.execute(() -> {
            String label = null;
            try {
                Geocoder geocoder = new Geocoder(this, Locale.getDefault());
                List<Address> results = geocoder.getFromLocation(center.getLatitude(), center.getLongitude(), 1);
                if (results != null && !results.isEmpty()) {
                    label = results.get(0).getAddressLine(0);
                }
            } catch (IOException | IllegalArgumentException ignored) {
                // Coordinates remain usable when address lookup is unavailable.
            }
            if (label != null && !label.trim().isEmpty()) {
                String resolvedLabel = label;
                mainHandler.post(() -> {
                    if (request == geocodeRequest && !isFinishing()) {
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
        if (latest != null) {
            mapView.getController().animateTo(new GeoPoint(latest.getLatitude(), latest.getLongitude()));
            mapView.getController().setZoom(17.0);
            scheduleReverseGeocode();
        } else {
            Toast.makeText(this, "Current location is not available yet.", Toast.LENGTH_SHORT).show();
        }
    }

    private boolean hasLocationPermission() {
        return Build.VERSION.SDK_INT < Build.VERSION_CODES.M
                || checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
    }

    private void confirmLocation() {
        GeoPoint center = mapView.getMapCenter() instanceof GeoPoint
                ? (GeoPoint) mapView.getMapCenter()
                : BENGALURU;
        confirmButton.setEnabled(false);
        addressText.setText("Finding address...");
        geocoderExecutor.execute(() -> {
            String selectedAddress = String.format(
                    Locale.getDefault(), "%.5f, %.5f", center.getLatitude(), center.getLongitude());
            try {
                Geocoder geocoder = new Geocoder(this, Locale.getDefault());
                List<Address> results = geocoder.getFromLocation(center.getLatitude(), center.getLongitude(), 1);
                if (results != null && !results.isEmpty() && results.get(0).getAddressLine(0) != null) {
                    selectedAddress = results.get(0).getAddressLine(0);
                }
            } catch (IOException | IllegalArgumentException ignored) {
                // Return coordinates if this device has no reverse geocoder.
            }
            String finalAddress = selectedAddress;
            mainHandler.post(() -> {
                if (isFinishing()) {
                    return;
                }
                Intent result = new Intent();
                result.putExtra("field", field);
                result.putExtra("address", finalAddress);
                result.putExtra("latitude", center.getLatitude());
                result.putExtra("longitude", center.getLongitude());
                setResult(RESULT_OK, result);
                finish();
            });
        });
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
    protected void onResume() {
        super.onResume();
        if (mapView != null) {
            mapView.onResume();
        }
    }

    @Override
    protected void onPause() {
        if (mapView != null) {
            mapView.onPause();
        }
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        mainHandler.removeCallbacks(reverseGeocodeAction);
        geocoderExecutor.shutdownNow();
        if (mapView != null) {
            mapView.onDetach();
        }
        super.onDestroy();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        if (mapView != null) {
            mapView.onSaveInstanceState(outState);
        }
    }
}
