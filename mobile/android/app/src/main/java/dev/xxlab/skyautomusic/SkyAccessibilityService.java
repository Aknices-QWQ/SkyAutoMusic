package dev.xxlab.skyautomusic;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.ComponentName;
import android.content.Context;
import android.graphics.Path;
import android.graphics.Point;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.WindowManager;
import android.view.accessibility.AccessibilityEvent;

import java.util.HashSet;
import java.util.List;
import java.util.Set;

/** Accessibility bridge used to send tap gestures without requiring a rooted phone. */
public class SkyAccessibilityService extends AccessibilityService {
    private static volatile SkyAccessibilityService instance;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    @Override
    protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
    }

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        // The player only needs the gesture API; window events are intentionally ignored.
    }

    @Override
    public void onInterrupt() {
        if (instance == this) {
            instance = null;
        }
    }

    @Override
    public boolean onUnbind(android.content.Intent intent) {
        if (instance == this) {
            instance = null;
        }
        return super.onUnbind(intent);
    }

    public static boolean isConnected() {
        return instance != null;
    }

    public static boolean isEnabled(Context context) {
        if (context == null) {
            return false;
        }
        String enabled = Settings.Secure.getString(
                context.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (TextUtils.isEmpty(enabled)) {
            return false;
        }
        ComponentName expected = new ComponentName(context, SkyAccessibilityService.class);
        String flattened = expected.flattenToString();
        for (String service : enabled.split(":")) {
            if (flattened.equalsIgnoreCase(service)) {
                return true;
            }
        }
        return false;
    }

    /** Dispatch all keys in one GestureDescription so chords land at the same instant. */
    public static boolean dispatchChord(final List<Integer> indexes, final float[] xs, final float[] ys) {
        final SkyAccessibilityService service = instance;
        if (service == null || indexes == null || xs == null || ys == null || xs.length < 15 || ys.length < 15) {
            return false;
        }
        service.mainHandler.post(() -> service.dispatchChordOnMain(indexes, xs, ys));
        return true;
    }

    private void dispatchChordOnMain(List<Integer> indexes, float[] xs, float[] ys) {
        if (indexes.isEmpty()) {
            return;
        }
        WindowManager manager = (WindowManager) getSystemService(WINDOW_SERVICE);
        if (manager == null || manager.getDefaultDisplay() == null) {
            return;
        }
        Point displaySize = new Point();
        manager.getDefaultDisplay().getRealSize(displaySize);
        int width = displaySize.x;
        int height = displaySize.y;
        if (width <= 0 || height <= 0) {
            return;
        }
        GestureDescription.Builder gesture = new GestureDescription.Builder();
        Set<Integer> unique = new HashSet<>();
        for (Integer rawIndex : indexes) {
            if (rawIndex == null || rawIndex < 0 || rawIndex >= 15 || !unique.add(rawIndex)) {
                continue;
            }
            float normalizedX = Math.max(0.01f, Math.min(0.99f, xs[rawIndex]));
            float normalizedY = Math.max(0.01f, Math.min(0.99f, ys[rawIndex]));
            Path path = new Path();
            path.moveTo(normalizedX * width, normalizedY * height);
            gesture.addStroke(new GestureDescription.StrokeDescription(path, 0, 1));
        }
        dispatchGesture(gesture.build(), null, null);
    }
}
